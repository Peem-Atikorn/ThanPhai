from datetime import datetime, timedelta, timezone

import pytest

from geo_integration import Area, Gazetteer
from hazard_agent import HazardAgent, understand
from hazard_agent.agent import HAZARD_TH, PREPARE_TH
from hazard_agent.understanding import detect_hazards, detect_window
from thanphai_shared import HazardType

BKK = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 19, 9, 0, tzinfo=BKK)
GAZ = Gazetteer(
    [
        Area("TH14", "province", "พระนครศรีอยุธยา", "Phra Nakhon Si Ayutthaya", None, 14.3, 100.5, 2500),
        Area("TH1406", "amphoe", "เสนา", "Sena", "TH14", 14.3, 100.4, 200),
        Area("TH12", "province", "นนทบุรี", "Nonthaburi", None, 13.9, 100.4, 600),
        Area("TH1205", "amphoe", "บางบัวทอง", "Bang Bua Thong", "TH12", 13.9, 100.4, 110),
    ]
)


class FakeTools:
    def __init__(self):
        self.calls = []

    def get_assessments(self, area_code):
        self.calls.append(area_code)
        return [
            {"hazard": "flood", "level": "CRITICAL", "observations": {"above_bank_m": 2.11}, "rule_ids": ["FLOOD_ABOVE_BANK_HIGH"], "sources": ["ThaiWater (สสน.)"]},
            {"hazard": "rain_storm", "level": "WATCH", "observations": {"rain_24h": 40.0}, "rule_ids": ["RAIN_HEAVY_24H"], "sources": ["ThaiWater (สสน.)"]},
            {"hazard": "fire", "level": "NORMAL", "observations": {"hotspots_24h": 0}, "rule_ids": ["RULES_NO_TRIGGER"], "sources": ["NASA FIRMS"]},
        ]

    def get_official_warnings(self):
        return [{"title": "ฝนตกหนักถึงหนักมากบริเวณประเทศไทย", "hazards": ["rain_storm", "flood"]}]


agent = HazardAgent(GAZ, FakeTools())


@pytest.mark.parametrize(
    ("text", "hazards"),
    [("น้ำจะท่วมไหม", [HazardType.FLOOD]), ("ฝนตกหนักไหม", [HazardType.RAIN_STORM]),
     ("ฝนทิ้งช่วงจะแล้งไหม", [HazardType.DROUGHT]), ("ฝุ่นควันไฟป่า", [HazardType.FIRE, HazardType.AIR_QUALITY]),
     ("ค่าฝุ่น pm2.5 วันนี้", [HazardType.AIR_QUALITY]), ("หมอกควันหนาไหม", [HazardType.AIR_QUALITY]),
     ("วันนี้อากาศร้อนจัดไหม", [HazardType.HEAT]), ("ระวังฮีตสโตรก", [HazardType.HEAT]),
     ("เมื่อกี้แผ่นดินไหวไหม", [HazardType.EARTHQUAKE]),
     ("พรุ่งนี้ออกเรือได้ไหม", [HazardType.MARINE]),
     ("พายุไต้ฝุ่นจะเข้าไทยไหม", [HazardType.RAIN_STORM, HazardType.TROPICAL_CYCLONE]),
     ("ไต้ฝุ่นมาแล้วฝุ่นก็ยังเยอะ", [HazardType.AIR_QUALITY, HazardType.TROPICAL_CYCLONE]),
     ("บ้านอยู่เชิงเขา กลัวดินถล่ม", [HazardType.LANDSLIDE]), ("rainy season", [HazardType.RAIN_STORM]),
     ("Will it flood?", [HazardType.FLOOD])],
)
def test_detect_hazards(text, hazards):
    assert detect_hazards(text) == hazards


def test_detect_window():
    start, end, explicit = detect_window("พรุ่งนี้ฝนตกไหม", NOW)
    assert start.date() == (NOW + timedelta(days=1)).date() and (end - start) == timedelta(days=1) and explicit
    start, end, explicit = detect_window("ภายใน 3 วัน", NOW)
    assert end - start == timedelta(days=3) and explicit
    assert detect_window("ฝนตกไหม", NOW)[2] is False


@pytest.mark.parametrize(
    ("question", "future"),
    [("พรุ่งนี้ฝนตกที่เสนาไหม", True), ("น้ำจะท่วมอำเภอเสนาไหม", True), ("3 วันข้างหน้าเสนาน้ำท่วมไหม", True),
     ("Will it flood in Sena?", True), ("ตอนนี้อำเภอเสนาน้ำท่วมไหม", False), ("อำเภอเสนาน้ำท่วมไหม", False),
     ("วันนี้เสนาฝนตกไหม", False), ("ตอนนี้น้ำจะท่วมเสนาไหม", False)],
)
def test_future_detection(question, future):
    assert understand(question, GAZ, NOW).future is future


def test_future_question_says_no_forecast():
    answer = agent.ask("พรุ่งนี้ฝนตกที่อำเภอเสนาไหม", NOW)
    assert "ยังไม่มีพยากรณ์ล่วงหน้าของฝนหนัก/พายุสำหรับวันที่ 20/10" in answer.text  # no forecast tool here
    assert "สถานการณ์ ณ ตอนนี้" in answer.text
    assert "ให้ยึดประกาศทางการเป็นหลัก" in answer.text  # TMD warning covers the coming days


def test_current_question_has_no_forecast_note():
    answer = agent.ask("ตอนนี้อำเภอเสนาน้ำท่วมไหม", NOW)
    assert "ยังไม่มีพยากรณ์" not in answer.text
    assert "สถานการณ์ล่าสุดจากข้อมูลตรวจวัด" in answer.text


@pytest.mark.parametrize(
    ("scope", "question", "included"),
    [
        ({"provinces_by_hazard": {"flood": ["TH90"]}}, "วันนี้อำเภอเสนาน้ำท่วมไหม", False),
        ({"provinces_by_hazard": {"flood": ["TH14"]}}, "วันนี้อำเภอเสนาน้ำท่วมไหม", True),
        # A province mentioned for rain must not inherit the same notice's flood warning.
        ({"provinces": ["TH14", "TH12"], "provinces_by_hazard": {"flood": ["TH12"], "rain_storm": ["TH14"]}},
         "วันนี้อำเภอเสนาน้ำท่วมไหม", False),
        ({"provinces_by_hazard": {}}, "วันนี้อำเภอเสนาน้ำท่วมไหม", False),
        ({"provinces": ["TH90"]}, "วันนี้อำเภอเสนาน้ำท่วมไหม", False),
        ({"provinces": ["TH14"]}, "วันนี้จังหวัดพระนครศรีอยุธยาน้ำท่วมไหม", True),
        ({"impact_from": "2026-10-18", "impact_to": "2026-10-18"}, "วันนี้อำเภอเสนาน้ำท่วมไหม", False),
        ({"impact_from": "2026-10-20", "impact_to": "2026-10-21"}, "วันนี้อำเภอเสนาน้ำท่วมไหม", False),
        ({"impact_from": "2026-10-18", "impact_to": "2026-10-19"}, "วันนี้อำเภอเสนาน้ำท่วมไหม", True),
        ({"impact_from": "2026-10-20", "impact_to": "2026-10-20"}, "พรุ่งนี้อำเภอเสนาน้ำท่วมไหม", True),
        ({"impact_from": "2026-10-21", "impact_to": "2026-10-21"}, "พรุ่งนี้อำเภอเสนาน้ำท่วมไหม", False),
        # The local statement's dates override the title's longer nationwide window.
        ({"impact_from": "2026-10-19", "impact_to": "2026-10-25",
          "provinces_by_hazard": {"flood": ["TH14"]},
          "impact_by_hazard": {"flood": {"TH14": ["2026-10-19", "2026-10-19"]}}},
         "พรุ่งนี้อำเภอเสนาน้ำท่วมไหม", False),
        ({"impact_from": "2026-10-21", "impact_to": "2026-10-25",
          "provinces_by_hazard": {"flood": ["TH14"]},
          "impact_by_hazard": {"flood": {"TH14": ["2026-10-20", "2026-10-20"]}}},
         "พรุ่งนี้อำเภอเสนาน้ำท่วมไหม", True),
        # Location and time must match the SAME hazard, not separate parts of a notice.
        ({"provinces_by_hazard": {"flood": ["TH14"], "rain_storm": ["TH12"]},
          "impact_by_hazard": {"flood": {"TH14": ["2026-10-21", "2026-10-21"]},
                               "rain_storm": {"TH12": ["2026-10-19", "2026-10-19"]}}},
         "วันนี้อำเภอเสนาน้ำท่วมและฝนตกไหม", False),
        # Keep compatibility with tools that do not supply scope metadata yet.
        ({}, "วันนี้อำเภอเสนาน้ำท่วมไหม", True),
    ],
)
def test_warning_matches_question_scope(scope, question, included):
    warning = {"title": "scope-test-warning", "hazards": ["rain_storm", "flood"], **scope}

    class ScopedTools(FakeTools):
        def get_official_warnings(self):
            return [warning]

    answer = HazardAgent(GAZ, ScopedTools()).ask(question, NOW)
    assert (warning["title"] in answer.text) is included
    assert (warning in answer.evidence) is included
    assert ("กรมอุตุนิยมวิทยา (TMD)" in answer.sources) is included
    if not included:
        assert "ประกาศของกรมอุตุนิยมวิทยาครอบคลุม" not in answer.text


def test_satellite_flood_reason():
    class SatTools(FakeTools):
        def get_assessments(self, area_code):
            return [{"hazard": "flood", "level": "WARNING", "observations": {"above_bank_m": None, "satellite_flood_cells_7d": 1558.0},
                     "rule_ids": ["SAT_FLOOD_7D_LARGE"], "sources": ["GISTDA (ดาวเทียม)"]}]

    a = HazardAgent(GAZ, SatTools()).ask("ตอนนี้อำเภอเสนาน้ำท่วมไหม", NOW)
    assert "น้ำท่วม: เตือนภัย — ดาวเทียมพบน้ำท่วมกระจายในพื้นที่ราว 194 ตร.กม. ใน 7 วัน" in a.text


class ForecastTools(FakeTools):
    def get_forecast(self, area_code):
        return [
            {"date": "2026-10-19", "rain_mm": 0.3, "level": "NORMAL", "cond_th": "เมฆเป็นส่วนมาก"},
            {"date": "2026-10-20", "rain_mm": 48.0, "level": "WATCH", "cond_th": "ฝนตกหนัก"},
            {"date": "2026-10-21", "rain_mm": 2.0, "level": "NORMAL", "cond_th": None},
        ]


def test_tomorrow_rain_uses_the_forecast():
    a = HazardAgent(GAZ, ForecastTools()).ask("พรุ่งนี้ฝนตกที่อำเภอเสนาไหม", NOW)
    assert "• พยากรณ์ฝน 20/10: เฝ้าระวัง — 48.0 มม. (ฝนตกหนัก)" in a.text
    assert "19/10" not in a.text.split("พยากรณ์ฝนรายวัน")[1].split("\n•")[0]  # only the asked day
    assert "ยังไม่มีพยากรณ์" not in a.text  # rain is covered by the forecast
    assert "get_forecast(TH1406)" in a.tool_calls


def test_flood_question_keeps_the_no_forecast_notice_but_shows_rain():
    a = HazardAgent(GAZ, ForecastTools()).ask("พรุ่งนี้น้ำจะท่วมอำเภอเสนาไหม", NOW)
    assert "ยังไม่มีพยากรณ์ล่วงหน้าของน้ำท่วม" in a.text
    assert "พยากรณ์ฝน 20/10" in a.text  # rain forecast still helps for flood


def test_three_day_window_lists_three_days():
    a = HazardAgent(GAZ, ForecastTools()).ask("3 วันข้างหน้าฝนตกที่เสนาไหม", NOW)
    assert a.text.count("• พยากรณ์ฝน") == 3


def test_tomorrow_uses_thai_date_not_utc():
    # 01:00 on 20 Oct in Thailand is still 19 Oct in UTC; "พรุ่งนี้" must mean 21 Oct.
    utc_clock = datetime(2026, 10, 19, 18, 0, tzinfo=timezone.utc)
    answer = agent.ask("พรุ่งนี้ฝนตกที่อำเภอเสนาไหม", utc_clock)
    assert "วันที่ 21/10" in answer.text


def test_understand_flood_question():
    u = understand("น้ำจะท่วมอำเภอเสนาไหม", GAZ, NOW)
    assert u.intent == "risk" and u.hazards == [HazardType.FLOOD] and u.area.code == "TH1406"


def test_answer_has_level_reason_warning_and_source():
    answer = agent.ask("น้ำจะท่วมอำเภอเสนาไหม", NOW)
    assert "อ.เสนา จ.พระนครศรีอยุธยา" in answer.text
    assert "น้ำท่วม: วิกฤต" in answer.text and "2.11" in answer.text
    assert "ประกาศกรมอุตุนิยมวิทยา" in answer.text
    assert "1784" in answer.text  # emergency numbers on WARNING/CRITICAL
    assert "ThaiWater (สสน.)" in answer.sources
    # Only the hazard that was asked about.
    assert "ไฟป่า" not in answer.text


def test_no_hazard_named_checks_all():
    answer = agent.ask("อำเภอเสนาเป็นยังไงบ้าง", NOW)
    assert "ไฟป่า/ไฟหญ้า: ปกติ" in answer.text
    assert "ภัยแล้ง: ยังไม่มีแบบจำลองหรือข้อมูล" in answer.text


def test_missing_area_asks_back():
    answer = agent.ask("น้ำจะท่วมไหม", NOW)
    assert "ไม่พบชื่อพื้นที่" in answer.text and answer.evidence == []


@pytest.fixture
def duplicate_districts():
    return Gazetteer([
        Area("TH31", "province", "บุรีรัมย์", "Buri Ram", None, 15.0, 103.0, 1000),
        Area("TH3123", "amphoe", "เฉลิมพระเกียรติ", "Chaloem Phra Kiat", "TH31", 14.6, 102.9, 100),
        Area("TH55", "province", "น่าน", "Nan", None, 18.8, 100.8, 1000),
        Area("TH5515", "amphoe", "เฉลิมพระเกียรติ", "Chaloem Phra Kiat", "TH55", 19.6, 101.1, 100),
    ])


@pytest.mark.parametrize("question", [
    "อำเภอเฉลิมพระเกียรติน้ำท่วมไหม",
    "ทำไมอำเภอเฉลิมพระเกียรติน้ำท่วม",
    "อำเภอเฉลิมพระเกียรติต้องเตรียมตัวรับน้ำท่วมอย่างไร",
    "Will it flood in Chaloem Phra Kiat?",
])
def test_duplicate_district_asks_before_assessing(duplicate_districts, question):
    tools = FakeTools()

    def must_not_rewrite(question, facts):
        raise AssertionError("Clarification must not call the LLM")

    answer = HazardAgent(duplicate_districts, tools, explainer=must_not_rewrite).ask(question, NOW)
    assert answer.intent == "clarify"
    assert "อ.เฉลิมพระเกียรติ จ.บุรีรัมย์" in answer.text
    assert "อ.เฉลิมพระเกียรติ จ.น่าน" in answer.text
    assert answer.area is None and answer.evidence == []
    assert tools.calls == []


@pytest.mark.parametrize(("question", "code"), [
    ("อำเภอเฉลิมพระเกียรติ จังหวัดบุรีรัมย์น้ำท่วมไหม", "TH3123"),
    ("อำเภอเฉลิมพระเกียรติ จังหวัดน่านน้ำท่วมไหม", "TH5515"),
    ("Will it flood in Chaloem Phra Kiat, Nan?", "TH5515"),
])
def test_province_resolves_duplicate_district(duplicate_districts, question, code):
    tools = FakeTools()
    answer = HazardAgent(duplicate_districts, tools).ask(question, NOW)
    assert answer.intent == "risk" and answer.area["code"] == code
    assert tools.calls == [code]


def test_duplicate_tambon_still_asks_for_location(duplicate_districts):
    gazetteer = Gazetteer(duplicate_districts.areas + [
        Area("TH312301", "tambon", "บ้านใหม่", "Ban Mai", "TH3123", 14.6, 102.9, 10),
        Area("TH551501", "tambon", "บ้านใหม่", "Ban Mai", "TH5515", 19.6, 101.1, 10),
    ])
    tools = FakeTools()
    answer = HazardAgent(gazetteer, tools).ask("ตำบลบ้านใหม่น้ำท่วมไหม", NOW)
    assert answer.intent == "clarify" and tools.calls == []
    assert "จ.บุรีรัมย์" in answer.text and "จ.น่าน" in answer.text


def test_same_name_at_two_levels_does_not_need_clarification():
    # Same-name district and subdistrict: without a prefix the gazetteer selects the district.
    gazetteer = Gazetteer(GAZ.areas + [
        Area("TH140601", "tambon", "เสนา", "Sena", "TH1406", 14.3, 100.4, 10),
    ])
    tools = FakeTools()
    answer = HazardAgent(gazetteer, tools).ask("เสนาน้ำท่วมไหม", NOW)
    assert answer.intent == "risk" and answer.area["code"] == "TH1406"
    assert tools.calls == ["TH1406"]


def test_emergency_with_duplicate_district_keeps_emergency_response(duplicate_districts):
    tools = FakeTools()
    answer = HazardAgent(duplicate_districts, tools).ask("ช่วยด้วย น้ำท่วมอำเภอเฉลิมพระเกียรติ", NOW)
    assert answer.intent == "emergency" and tools.calls == []
    assert "1784" in answer.text and "1669" in answer.text


@pytest.mark.parametrize(("question", "code"), [
    ("นนทบุรีกับปทุมธานีน้ำท่วมไหม", "TH13"),
    ("อำเภอเสนาและอำเภอบางบาลน้ำท่วมไหม", "TH1405"),
    ("เชียงใหม่ เชียงราย ไฟป่าไหม", "TH50"),
])
def test_different_named_areas_are_not_duplicate_names(question, code):
    gazetteer = Gazetteer(GAZ.areas + [
        Area("TH13", "province", "ปทุมธานี", "Pathum Thani", None, 14.0, 100.5, 1000),
        Area("TH1405", "amphoe", "บางบาล", "Bang Ban", "TH14", 14.4, 100.5, 100),
        Area("TH50", "province", "เชียงใหม่", "Chiang Mai", None, 18.8, 99.0, 1000),
        Area("TH57", "province", "เชียงราย", "Chiang Rai", None, 19.9, 99.8, 1000),
    ])
    tools = FakeTools()
    answer = HazardAgent(gazetteer, tools).ask(question, NOW)
    assert answer.intent == "risk" and answer.area["code"] == code
    assert "ambiguous area" not in answer.notes
    assert tools.calls == [code]


@pytest.fixture
def five_duplicate_districts(duplicate_districts):
    return Gazetteer(duplicate_districts.areas + [
        Area("TH19", "province", "สระบุรี", "Saraburi", None, 14.5, 100.9, 1000),
        Area("TH1913", "amphoe", "เฉลิมพระเกียรติ", "Chaloem Phra Kiat", "TH19", 14.5, 100.9, 100),
        Area("TH30", "province", "นครราชสีมา", "Nakhon Ratchasima", None, 15.0, 102.0, 1000),
        Area("TH3032", "amphoe", "เฉลิมพระเกียรติ", "Chaloem Phra Kiat", "TH30", 15.0, 102.0, 100),
        Area("TH80", "province", "นครศรีธรรมราช", "Nakhon Si Thammarat", None, 8.4, 99.9, 1000),
        Area("TH8023", "amphoe", "เฉลิมพระเกียรติ", "Chaloem Phra Kiat", "TH80", 8.4, 99.9, 100),
    ])


def test_more_than_four_duplicate_names_requests_province(five_duplicate_districts):
    tools = FakeTools()
    answer = HazardAgent(five_duplicate_districts, tools).ask("อำเภอเฉลิมพระเกียรติน้ำท่วมไหม", NOW)
    assert answer.intent == "clarify" and tools.calls == []
    assert "อีก 1 แห่ง" in answer.text and "ระบุจังหวัด" in answer.text


def test_fifth_duplicate_can_be_selected_by_province(five_duplicate_districts):
    tools = FakeTools()
    answer = HazardAgent(five_duplicate_districts, tools).ask(
        "อำเภอเฉลิมพระเกียรติ จังหวัดนครศรีธรรมราชน้ำท่วมไหม", NOW)
    assert answer.intent == "risk" and answer.area["code"] == "TH8023"
    assert tools.calls == ["TH8023"]


def test_clarification_options_only_contain_matching_names(duplicate_districts):
    gazetteer = Gazetteer(duplicate_districts.areas + GAZ.areas)
    answer = HazardAgent(gazetteer, FakeTools()).ask("อำเภอเฉลิมพระเกียรติและอำเภอเสนาน้ำท่วมไหม", NOW)
    assert answer.intent == "clarify"
    assert "จ.บุรีรัมย์" in answer.text and "จ.น่าน" in answer.text
    assert "อ.เสนา" not in answer.text


def test_out_of_scope():
    assert agent.ask("ราคาทองวันนี้เท่าไหร่", NOW).intent == "out_of_scope"


def test_emergency_gets_numbers_only():
    answer = agent.ask("ช่วยด้วย น้ำท่วมบ้านติดอยู่บนหลังคา", NOW)
    assert answer.intent == "emergency" and "1784" in answer.text and "1669" in answer.text


def test_prepare_without_area():
    answer = agent.ask("ต้องเตรียมตัวยังไงถ้าน้ำจะท่วม", NOW)
    assert answer.intent == "prepare" and "ของมีค่า" in answer.text
    assert "แหล่งอ้างอิง: [ปภ." in answer.text
    assert "คำแนะนำเพิ่มเติม (ไม่ได้มาจากคู่มือราชการ):" in answer.text
    assert "ชาร์จโทรศัพท์มือถือ" in answer.text
    # Verify team tip does not appear inside or before the official citation line
    parts = answer.text.split("คำแนะนำเพิ่มเติม (ไม่ได้มาจากคู่มือราชการ):")
    assert len(parts) == 2
    assert "แหล่งอ้างอิง: [ปภ." in parts[0]
    assert "ชาร์จโทรศัพท์มือถือ" in parts[1]
    assert "ทีมทันภัย" not in parts[0]


def test_prepare_storm_gets_window_safety_tip():
    answer = agent.ask("พายุฝนฟ้าคะนองลมแรงต้องเตรียมตัวอย่างไร", NOW)
    assert answer.intent == "prepare"
    assert "แหล่งอ้างอิง: [ปภ." in answer.text
    assert "คำแนะนำเพิ่มเติม (ไม่ได้มาจากคู่มือราชการ):" in answer.text
    assert "หน้าต่างกระจก" in answer.text
    parts = answer.text.split("คำแนะนำเพิ่มเติม (ไม่ได้มาจากคู่มือราชการ):")
    assert len(parts) == 2
    assert "แหล่งอ้างอิง: [ปภ." in parts[0]
    assert "หน้าต่างกระจก" in parts[1]
    assert "ทีมทันภัย" not in parts[0]


def test_prepare_evacuation_gets_critical_passage():
    answer = agent.ask("น้ำท่วมสูงมาก ต้องอพยพอย่างไร", NOW)
    assert answer.intent == "prepare"
    assert "อพยพ" in answer.text
    assert "เด็กและคนชรา" in answer.text or "เส้นทางที่ปลอดภัย" in answer.text
    assert "หน้า 13–14" in answer.text


def test_prepare_with_area_uses_area_level():
    answer = agent.ask("น้ำจะท่วมอำเภอเสนา ต้องเตรียมตัวอย่างไร", NOW)
    assert answer.intent == "prepare"
    assert "น้ำท่วม: วิกฤต" in answer.text  # ส่วนสถานการณ์จาก _assess ยังอยู่
    assert "อพยพ" in answer.text  # FakeTools ให้ CRITICAL -> ต้องได้ passage อพยพ
    assert "วาล์วก๊าซ" not in answer.text


def test_prepare_fire_includes_prepare_th():
    answer = agent.ask("ไฟป่าต้องเตรียมตัวอย่างไร", NOW)
    assert answer.intent == "prepare"
    # Fire advice must include items from PREPARE_TH
    assert any(step in answer.text for step in ["งดเผาในที่โล่ง", "หน้ากาก N95", "1362"])
    assert "คำแนะนำเพิ่มเติม (ไม่ได้มาจากคู่มือราชการ):" in answer.text
    assert not any("แหล่ง" in line and "ปภ." in line for line in answer.text.splitlines())


def test_prepare_drought_includes_prepare_th_and_no_gas_valve():
    answer = agent.ask("ภัยแล้งต้องเตรียมตัวอย่างไร", NOW)
    assert answer.intent == "prepare"
    # Drought advice must include items from PREPARE_TH
    assert any(step in answer.text for step in ["กักเก็บน้ำสำรอง", "ใช้น้ำอย่างประหยัด", "วางแผนการเพาะปลูก"])
    assert "คำแนะนำเพิ่มเติม (ไม่ได้มาจากคู่มือราชการ):" in answer.text
    # Drought advice must not contain gas valve shutoff
    assert "วาล์วก๊าซ" not in answer.text
    assert "ท่อแก๊ส" not in answer.text


def test_prepare_without_rag_uses_fallback(monkeypatch):
    import hazard_agent.agent
    monkeypatch.setattr(hazard_agent.agent, "get_disaster_guidance", None)
    answer = agent.ask("ต้องเตรียมตัวยังไงถ้าน้ำจะท่วม", NOW)
    assert answer.intent == "prepare"
    assert "ยกของมีค่า" in answer.text
    assert "คำแนะนำเพิ่มเติม (ไม่ได้มาจากคู่มือราชการ):" in answer.text


def test_prepare_all_hazards_never_lists_team_steps_under_ddpm_citation():
    text = agent.ask("อำเภอเสนา ต้องเตรียมตัวอย่างไร", NOW).text
    for hazard in ("drought", "fire"):
        block = text.split(f"การเตรียมพร้อมรับ{HAZARD_TH[hazard]}:")[1].split("การเตรียมพร้อมรับ")[0]
        first_team_step = PREPARE_TH[hazard][0]
        assert block.index("คำแนะนำเพิ่มเติม (ไม่ได้มาจากคู่มือราชการ):") < block.index(first_team_step)


def test_prepare_all_hazards_shows_shared_passage_once():
    text = agent.ask("ต้องเตรียมตัวรับมือภัยพิบัติอย่างไร", NOW).text
    assert text.count("กระเป๋าเสื้อ") <= 1


def test_explainer_rewrites_and_keeps_facts():
    calls = []

    def fake_llm(question, facts):
        calls.append((question, facts))
        return "เสนาน้ำท่วมวิกฤต"

    a = HazardAgent(GAZ, FakeTools(), explainer=fake_llm).ask("น้ำจะท่วมอำเภอเสนาไหม", NOW)
    assert a.llm_used and a.text.startswith("เสนาน้ำท่วมวิกฤต\n")
    # Warning, emergency numbers and sources are appended word for word from the rule-based answer.
    assert "⚠ ประกาศกรมอุตุนิยมวิทยา: ฝนตกหนักถึงหนักมากบริเวณประเทศไทย" in a.text
    assert "1784" in a.text and "ที่มา: ThaiWater (สสน.)" in a.text
    assert "2.11" in a.facts and calls[0][0] == "น้ำจะท่วมอำเภอเสนาไหม"


def test_follow_up_without_a_place_uses_the_open_area():
    # "แล้วมีโอกาสฝนตกกี่เปอร์เซ็น" after a question about Sena (or with Sena picked on the map).
    tools = FakeTools()
    a = HazardAgent(GAZ, tools).ask("แล้วฝนตกไหม", NOW, area_code="TH1406")
    assert a.intent == "risk" and a.area["code"] == "TH1406" and tools.calls == ["TH1406"]
    assert "area from the open area" in a.notes


@pytest.mark.parametrize("question", ["อุณหภูมิตอนเที่ยงจะเท่าไหร่", "ตอนนี้กี่องศา", "พรุ่งนี้ร้อนไหม"])
def test_temperature_follow_up_uses_the_open_area(question):
    # Seen live: after "ธัญบุรีฝนตกไหม", a temperature question went to small talk and asked for the area again.
    tools = FakeTools()
    a = HazardAgent(GAZ, tools).ask(question, NOW, area_code="TH1406")
    assert a.intent == "risk" and a.area["code"] == "TH1406" and tools.calls == ["TH1406"]
    assert understand(question, GAZ, NOW).hazards == [HazardType.HEAT]


@pytest.mark.parametrize("question", ["อำเภอเสนามีจุดความร้อนไหม", "any hotspot near Sena?"])
def test_fire_hotspot_question_is_a_fire_question(question):
    # "มีจุดความร้อนไหม" contains "ร้อนไหม", but a hotspot is the satellite fire data.
    assert detect_hazards(question) == [HazardType.FIRE]


def test_hotspot_and_heat_in_one_question():
    assert detect_hazards("อำเภอเสนามีจุดความร้อนไหม และร้อนไหม") == [HazardType.FIRE, HazardType.HEAT]


def test_heat_answer_gives_this_hour_before_the_peak():
    # "ตอนนี้กี่องศา" asks about now, not only about the hottest hour of the next 24 h.
    class HeatTools(FakeTools):
        def get_assessments(self, area_code):
            return [{"hazard": "heat", "level": "WATCH", "rule_ids": ["HEAT_WATCH"], "sources": ["Open-Meteo"],
                     "observations": {"heat_index_now": 31.4, "heat_index_max_24h": 38.2}}]

    text = HazardAgent(GAZ, HeatTools()).ask("ตอนนี้กี่องศา", NOW, area_code="TH1406").text
    now = "ตอนนี้ร้อนเหมือน 31.4°C (ดัชนีความร้อน ค่าคาดการณ์ชั่วโมงนี้)"
    assert now in text and "ดัชนีความร้อนสูงสุดใน 24 ชม. ข้างหน้า 38.2°C" in text
    assert text.index(now) < text.index("38.2")


def test_rain_chance_reason_names_the_peak_hour():
    class RainTools(FakeTools):
        def get_assessments(self, area_code):
            return [{"hazard": "rain_storm", "level": "NORMAL", "rule_ids": ["RULES_NO_TRIGGER"], "sources": ["Open-Meteo (แบบจำลองโอกาสฝน)"],
                     "observations": {"rain_24h": 0.0, "rain_chance_now": 5.0, "rain_chance_max_24h": 63.0, "rain_chance_peak_hour": 18.0}}]

    a = HazardAgent(GAZ, RainTools()).ask("อำเภอเสนาฝนตกไหม", NOW)
    assert "โอกาสฝนตกสูงสุดใน 24 ชม. ข้างหน้า 63% ราว 18:00 น. (แบบจำลอง Open-Meteo)" in a.text


def test_named_place_wins_over_the_open_area():
    a = HazardAgent(GAZ, FakeTools()).ask("น้ำจะท่วมอำเภอบางบัวทองไหม", NOW, area_code="TH1406")
    assert a.area["code"] == "TH1205"


def test_unknown_or_missing_open_area_still_asks_where():
    agent_ = HazardAgent(GAZ, FakeTools())
    assert agent_.ask("ฝนตกไหม", NOW, area_code="TH9999").intent == "clarify"
    assert agent_.ask("ฝนตกไหม", NOW).intent == "clarify"


def test_rewrite_that_copies_the_warning_shows_it_once():
    # Seen live: the model repeated the TMD warning without "⚠" and with other spacing, above the verbatim copy.
    def copying_llm(question, facts):
        warning = next(line for line in facts.splitlines() if line.startswith("⚠"))
        copied = "  ".join(warning.removeprefix("⚠ ").split(" "))
        return f"เสนาน้ำท่วมวิกฤต\n{copied}\n• ที่มา: ThaiWater (สสน.)"

    a = HazardAgent(GAZ, FakeTools(), explainer=copying_llm).ask("น้ำจะท่วมอำเภอเสนาไหม", NOW)
    assert a.llm_used and a.text.startswith("เสนาน้ำท่วมวิกฤต\n")
    assert a.text.count("ฝนตกหนักถึงหนักมากบริเวณประเทศไทย") == 1
    assert a.text.count("ที่มา:") == 1
    assert "⚠ ประกาศกรมอุตุนิยมวิทยา: ฝนตกหนักถึงหนักมากบริเวณประเทศไทย" in a.text


def test_rewrite_with_a_shortened_warning_title_keeps_only_the_verbatim_one():
    # Seen live: the model dropped part of the title ("(มีผลกระทบ…)"); a TMD title is shown only word for word.
    def shortening_llm(question, facts):
        warning = next(line for line in facts.splitlines() if line.startswith("⚠"))
        return f"เสนาน้ำท่วมวิกฤต\n**ประกาศกรมอุตุนิยมวิทยา:** {warning.split(': ', 1)[1][:12]}"

    a = HazardAgent(GAZ, FakeTools(), explainer=shortening_llm).ask("น้ำจะท่วมอำเภอเสนาไหม", NOW)
    assert a.llm_used and a.text.startswith("เสนาน้ำท่วมวิกฤต\n")
    assert a.text.count("ประกาศกรมอุตุนิยมวิทยา:") == 1
    assert "⚠ ประกาศกรมอุตุนิยมวิทยา: ฝนตกหนักถึงหนักมากบริเวณประเทศไทย" in a.text


def test_explainer_rejection_keeps_rule_answer():
    a = HazardAgent(GAZ, FakeTools(), explainer=lambda q, f: None).ask("น้ำจะท่วมอำเภอเสนาไหม", NOW)
    assert not a.llm_used and "น้ำท่วม: วิกฤต" in a.text


def test_explainer_not_used_for_emergency_or_refusals():
    def must_not_run(q, f):
        raise AssertionError("LLM must not rewrite emergency or out-of-scope answers")

    agent_llm = HazardAgent(GAZ, FakeTools(), explainer=must_not_run)
    assert "1784" in agent_llm.ask("ช่วยด้วย น้ำท่วมบ้านติดอยู่", NOW).text
    assert agent_llm.ask("ราคาทองวันนี้เท่าไหร่", NOW).intent == "out_of_scope"


DAMS = [
    {"name_th": "หนองปลาไหล", "name_en": "Nong Pla Lai", "province_code": "TH21", "province_th": "ระยอง", "storage_percent": 112.39,
     "status": "HIGH", "status_th": "น้ำเกินระดับกักเก็บปกติ", "storage_mcm": 184.04, "usable_percent": 104.15,
     "inflow_mcm": 3.2, "released_mcm": 3.68, "date": "2026-10-19", "agency_th": "ชป.", "lat": 12.9, "lon": 101.3},
    {"name_th": "ป่าสักชลสิทธิ์", "name_en": "Pa Sak Jolasid", "province_code": "TH16", "province_th": "ลพบุรี", "storage_percent": 60.0,
     "status": "NORMAL", "status_th": "น้ำพอใช้", "storage_mcm": 577.0, "usable_percent": 58.0,
     "inflow_mcm": 10.0, "released_mcm": 8.0, "date": "2026-10-19", "agency_th": "ชป.", "lat": 14.8, "lon": 101.1},
    {"name_th": "ลำนางรอง", "name_en": "Lam Nang Rong", "province_code": "TH31", "province_th": "บุรีรัมย์", "storage_percent": 31.0,
     "status": "LOW_CRITICAL", "status_th": "น้ำใช้การได้เหลือน้อย", "storage_mcm": 62.0, "usable_percent": 28.0,
     "inflow_mcm": 0.2, "released_mcm": 0.05, "date": "2026-10-19", "agency_th": "ชป.", "lat": 14.3, "lon": 102.6},
]


class DamTools(FakeTools):
    def get_dams(self):
        return DAMS


def test_dam_by_name_gives_exact_numbers():
    a = HazardAgent(GAZ, DamTools()).ask("เขื่อนหนองปลาไหลมีน้ำเท่าไหร่", NOW)
    assert a.intent == "dam" and not a.llm_used
    assert "• เขื่อนหนองปลาไหล จ.ระยอง: น้ำเกินระดับกักเก็บปกติ มีน้ำ 112% ของระดับกักเก็บปกติ (184 ล้าน ลบ.ม.)" in a.text
    # Daily volume plus the average rate: 3.68 million m³/day ≈ 43 m³/s.
    assert "ระบายออกวันนี้ 3.68 ล้าน ลบ.ม. (เฉลี่ยราว 43 ลบ.ม./วินาที)" in a.text
    assert "ข้อมูลรายวันของวันที่ 19/10/2569" in a.text and a.area["lat"] == 12.9


def test_dam_national_summary_counts_high_and_low():
    a = HazardAgent(GAZ, DamTools()).ask("ตอนนี้เขื่อนต่างๆ มีน้ำเป็นยังไงบ้าง", NOW)
    assert "มีน้ำใกล้เต็มหรือเกินระดับกักเก็บปกติ 1 แห่ง และมีน้ำที่ใช้การได้เหลือน้อย 1 แห่ง" in a.text
    assert "น้ำมาก" not in a.text and "วิกฤต" not in a.text  # plain wording, no stiff labels
    assert "หนองปลาไหล" in a.text and "ลำนางรอง" in a.text


def test_the_word_dam_is_not_the_tambon_named_khuean():
    gaz = Gazetteer([*GAZ.areas, Area("TH440310", "tambon", "เขื่อน", "Khuean", "TH4403", 16.2, 103.3, 30)])
    a = HazardAgent(gaz, DamTools()).ask("ตอนนี้เขื่อนทั่วประเทศเป็นยังไง", NOW)
    assert a.text.startswith("ตอนนี้เขื่อนขนาดใหญ่ 3 แห่ง")  # national summary, not "no dam in ต.เขื่อน"
    assert "รายงานโดย ชป." in a.text


def test_which_dam_releases_most():
    a = HazardAgent(GAZ, DamTools()).ask("วันนี้เขื่อนไหนระบายน้ำมากที่สุด", NOW)
    lines = a.text.splitlines()
    assert lines[0].startswith("เขื่อนที่ระบายน้ำมากที่สุดวันนี้ (รวมทุกเขื่อนขนาดใหญ่ระบาย 11.73 ล้าน ลบ.ม.")
    order = [n for n in ("ป่าสักชลสิทธิ์", "หนองปลาไหล", "ลำนางรอง") if n in a.text]
    assert a.text.index("ป่าสักชลสิทธิ์") < a.text.index("หนองปลาไหล") < a.text.index("ลำนางรอง") and len(order) == 3


def test_dam_question_for_a_province_without_dams():
    a = HazardAgent(GAZ, DamTools()).ask("อยุธยามีเขื่อนไหม", NOW)
    assert "ไม่มีเขื่อนขนาดใหญ่ใน" in a.text


def test_dam_answer_is_reworded_from_the_real_numbers_and_keeps_its_source_line():
    seen = {}

    def reword(q, facts):
        seen["facts"] = facts
        return "เขื่อนหนองปลาไหลมีน้ำอยู่ตามข้อมูลล่าสุดครับ"

    answer = HazardAgent(GAZ, DamTools(), explainer=reword).ask("Nong Pla Lai dam level?", NOW)
    assert answer.intent == "dam" and answer.llm_used
    assert answer.facts == seen["facts"] and "หนองปลาไหล" in seen["facts"]
    sources = [line for line in answer.facts.splitlines() if line.startswith("ที่มา:")]
    assert all(line in answer.text for line in sources)


def test_dam_answer_keeps_the_template_when_the_llm_invents_a_number():
    from decision_engine.explainer import QwenConfig, QwenExplainer

    inventing = QwenExplainer(QwenConfig(), requester=lambda q, facts: "เขื่อนมีน้ำ 99999 ล้าน ลบ.ม. ครับ")
    answer = HazardAgent(GAZ, DamTools(), explainer=inventing).ask("Nong Pla Lai dam level?", NOW)
    assert answer.intent == "dam" and not answer.llm_used and "99999" not in answer.text


def test_english_question():
    answer = agent.ask("Will it flood in Bang Bua Thong?", NOW)
    assert answer.area["code"] == "TH1205"


RIVERS = [
    {"id": "2611", "code": "C.36", "name_th": "บ้านบางหลวงโดด", "river_th": "แม่น้ำน้อย", "province_code": "TH14", "province_th": "พระนครศรีอยุธยา",
     "discharge_m3s": 663.0, "capacity_percent": 150, "above_bank_m": 1.37, "level_change_m": -0.01, "status": "OVERFLOW",
     "chain_order": 7, "agency": "RID", "time": "2026-10-19T17:00:00+07:00", "lat": 14.4, "lon": 100.4},
    {"id": "2609", "code": "C.35", "name_th": "บ้านป้อม", "river_th": "แม่น้ำเจ้าพระยา", "province_code": "TH14", "province_th": "พระนครศรีอยุธยา",
     "discharge_m3s": 1182.0, "capacity_percent": 102, "above_bank_m": 0.09, "level_change_m": 0.0, "status": "OVERFLOW",
     "chain_order": 6, "agency": "RID", "time": "2026-10-19T17:00:00+07:00", "lat": 14.3, "lon": 100.5},
    {"id": "2744", "code": "C.13", "name_th": "ท้ายเขื่อนเจ้าพระยา", "river_th": "แม่น้ำเจ้าพระยา", "province_code": "TH18", "province_th": "ชัยนาท",
     "discharge_m3s": 1800.0, "capacity_percent": 66, "above_bank_m": -2.29, "level_change_m": 0.0, "status": "NORMAL",
     "chain_order": 3, "agency": "RID", "time": "2026-10-19T17:00:00+07:00", "lat": 15.16, "lon": 100.19},
    {"id": "2723", "code": "C.3", "name_th": "บ้านบางพุทรา", "river_th": "แม่น้ำเจ้าพระยา", "province_code": "TH17", "province_th": "สิงห์บุรี",
     "discharge_m3s": 1934.0, "capacity_percent": 65, "above_bank_m": -2.52, "level_change_m": 0.05, "status": "NORMAL",
     "chain_order": 4, "agency": "RID", "time": "2026-10-19T17:00:00+07:00", "lat": 14.9, "lon": 100.4},
]


class RiverTools(DamTools):
    def get_rivers(self):
        return RIVERS


river_agent = HazardAgent(GAZ, RiverTools())


@pytest.mark.parametrize(
    ("question", "intent"),
    [("แม่น้ำเจ้าพระยาตอนนี้เป็นยังไง", "river"), ("C.13 น้ำไหลเท่าไหร่", "river"), ("ตอนนี้น้ำล้นตลิ่งที่ไหนบ้าง", "river"),
     ("อัตราการไหลที่อยุธยา", "river"),
     # A named district keeps the flood assessment, which already uses the bank level.
     ("อำเภอเสนาน้ำล้นตลิ่งไหม", "risk"),
     # "pm2.5" looks like a station code ("M.2") only without the guard around the regex.
     ("ค่าฝุ่น pm2.5 ที่อำเภอเสนา", "risk")],
)
def test_river_intent(question, intent):
    assert understand(question, GAZ, NOW).intent == intent


def test_chao_phraya_chain_upstream_to_downstream():
    a = river_agent.ask("แม่น้ำเจ้าพระยาตอนนี้เป็นยังไง", NOW)
    assert a.intent == "river" and a.text.startswith("แม่น้ำเจ้าพระยาและสาขา จากเหนือลงใต้:")
    assert a.text.index("C.13") < a.text.index("C.3 ") < a.text.index("C.35") < a.text.index("C.36")
    assert "• C.36 บ้านบางหลวงโดด (แม่น้ำน้อย) จ.พระนครศรีอยุธยา: น้ำไหลผ่าน 663 ลบ.ม./วินาที (150% ของความจุลำน้ำ) · ล้นตลิ่ง 1.37 ม." in a.text
    assert "ข้อมูลรายชั่วโมง ณ 17:00 น. วันที่ 19/10/2569" in a.text and "สถานีของกรมชลประทาน" in a.text


def test_station_code_matches_whole_code_only():
    a = river_agent.ask("สถานี C.35 ไหลเท่าไหร่", NOW)
    assert "C.35 บ้านป้อม" in a.text and "C.3 " not in a.text and a.area["code"] == "TH14"


def test_over_bank_stations_sorted_by_height():
    a = river_agent.ask("ตอนนี้น้ำล้นตลิ่งที่ไหนบ้าง", NOW)
    assert a.text.startswith("ตอนนี้มีสถานีวัดน้ำที่น้ำล้นตลิ่ง 2 แห่ง")
    assert a.text.index("C.36") < a.text.index("C.35") and "C.13" not in a.text


def test_chao_phraya_dam_release_is_the_flow_below_it():
    a = river_agent.ask("เขื่อนเจ้าพระยาระบายน้ำเท่าไหร่", NOW)
    assert a.intent == "river" and "เขื่อนทดน้ำ ไม่มีอ่างเก็บน้ำ" in a.text
    assert "C.13 ท้ายเขื่อนเจ้าพระยา" in a.text and "1,800 ลบ.ม./วินาที" in a.text


def test_river_answer_falls_back_to_the_template_when_the_llm_declines():
    plain = HazardAgent(GAZ, RiverTools()).ask("C.13 ไหลเท่าไหร่", NOW)
    answer = HazardAgent(GAZ, RiverTools(), explainer=lambda q, f: None).ask("C.13 ไหลเท่าไหร่", NOW)
    assert answer.intent == "river" and not answer.llm_used and answer.text == plain.text


class ChatExplainer:
    class config:
        model = "thanphai-qwen"

    def __init__(self, reply):
        self.reply = reply

    def __call__(self, question, facts):
        return None

    def chat(self, question):
        return self.reply


def test_small_talk_is_answered_by_the_llm_with_its_model_name():
    answer = HazardAgent(GAZ, RiverTools(), explainer=ChatExplainer("สวัสดีครับ น้องทันภัยเองครับ")).ask("สวัสดีครับ", NOW)
    assert answer.intent == "greeting" and answer.llm_used and answer.llm_model == "thanphai-qwen"
    assert answer.text == "สวัสดีครับ น้องทันภัยเองครับ"


def test_small_talk_falls_back_to_the_fixed_text_without_an_llm_reply():
    answer = HazardAgent(GAZ, RiverTools(), explainer=ChatExplainer(None)).ask("วันนี้กินอะไรดี", NOW)
    assert answer.intent == "out_of_scope" and not answer.llm_used and "ทันภัยตอบได้เฉพาะ" in answer.text


def test_the_word_river_is_not_the_tambon_named_mae_nam():
    gaz = Gazetteer([*GAZ.areas, Area("TH840405", "tambon", "แม่น้ำ", "Mae Nam", "TH8404", 9.57, 99.99, 40)])
    assert understand("แม่น้ำเจ้าพระยาตอนนี้เป็นยังไง", gaz, NOW).intent == "river"
    named = understand("ต.แม่น้ำ ฝนตกไหม", gaz, NOW)
    assert named.intent == "risk" and named.area.code == "TH840405"  # written as a place, it is one


def test_reason_names_an_upcoming_tmd_warning():
    from hazard_agent.agent import _reason

    assert _reason({"rule_ids": ["OFFICIAL_WARNING"]}) == "มีประกาศเตือนจากกรมอุตุนิยมวิทยา"
    assert "ล่วงหน้า" in _reason({"rule_ids": ["OFFICIAL_WARNING_UPCOMING"]})
    assert "ครอบคลุมทั้งภาค" in _reason({"rule_ids": ["OFFICIAL_WARNING_REGIONAL"]})
    assert _reason({"rule_ids": ["OFFICIAL_WARNING_HEAVY_RAIN"]}).endswith("มีฝนตกหนัก")
    assert _reason({"rule_ids": ["OFFICIAL_WARNING_VERY_HEAVY_RAIN"]}).endswith("มีฝนตกหนักมาก")


@pytest.mark.parametrize("question", ["วันนี้ฝนตกไหม", "วันนี้สามารถตากผ้าได้ไหม", "พรุ่งนี้ต้องพกร่มไหม", "น้ำจะท่วมไหม"])
def test_a_hazard_or_daily_weather_question_without_a_place_asks_where(question):
    answer = agent.ask(question, NOW)
    assert answer.intent == "clarify" and answer.area is None and answer.evidence == []
    assert "อำเภอหรือจังหวัดไหน" in answer.text


def test_daily_weather_question_with_a_place_is_a_rain_risk_question():
    u = understand("วันนี้ตากผ้าที่อำเภอเสนาได้ไหม", GAZ, NOW)
    assert u.intent == "risk" and u.hazards == [HazardType.RAIN_STORM] and u.area.code == "TH1406"


def test_national_and_regional_questions_still_get_the_overview():
    assert understand("สรุปฝนตกหนักทั้งประเทศ", GAZ, NOW).intent == "overview"


def test_a_source_line_the_llm_copies_is_not_shown_twice():
    def reword(q, facts):
        source = next(line for line in facts.splitlines() if line.startswith("ที่มา:"))
        return f"เขื่อนหนองปลาไหลมีน้ำตามข้อมูลล่าสุดครับ\n{source}"

    answer = HazardAgent(GAZ, DamTools(), explainer=reword).ask("Nong Pla Lai dam level?", NOW)
    assert answer.llm_used and answer.text.count("ที่มา:") == answer.facts.count("ที่มา:") == 1


@pytest.mark.parametrize("question", ["อยุธยา ของกินอร่อยมั้ย", "เสนามีที่เที่ยวไหม", "โรงแรมในนนทบุรีราคาเท่าไหร่"])
def test_off_topic_question_about_a_place_is_redirected_not_reported(question):
    tools = FakeTools()
    answer = HazardAgent(GAZ, tools).ask(question, NOW)
    assert answer.intent == "out_of_scope" and answer.area is not None
    assert "แนะนำไม่ได้" in answer.text and "ฝนตกไหม" in answer.text
    assert tools.calls == []  # no hazard lookup, so no hazard report


def test_hazard_question_with_a_travel_word_stays_a_risk_question():
    assert understand("ไปเที่ยวอยุธยาฝนจะตกไหม", GAZ, NOW).intent == "risk"
    assert understand("ที่เที่ยวอยุธยาน้ำท่วมไหม", GAZ, NOW).intent == "risk"
