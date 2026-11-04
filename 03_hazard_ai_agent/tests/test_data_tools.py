from datetime import datetime, timedelta, timezone
import pytest

from geo_integration import Area, Gazetteer
from hazard_agent import HazardAgent, understand

BKK = timezone(timedelta(hours=7))
NOW = datetime(2026, 11, 4, 9, 0, tzinfo=BKK)
GAZ = Gazetteer([
    Area("TH50", "province", "เชียงใหม่", "Chiang Mai", None, 18.8, 98.9, 20000),
    Area("TH5001", "amphoe", "เมืองเชียงใหม่", "Mueang Chiang Mai", "TH50", 18.8, 98.9, 1000),
    Area("TH14", "province", "พระนครศรีอยุธยา", "Phra Nakhon Si Ayutthaya", None, 14.3, 100.5, 2500),
    Area("TH1406", "amphoe", "เสนา", "Sena", "TH14", 14.3, 100.4, 200),
])


class DataTools:
    def get_earthquakes(self):
        return [{"magnitude": 4.2, "place_th": "เชียงใหม่", "time": "2026-11-04T01:00:00+00:00", "source": "USGS"}]

    def get_storms(self):
        return [{"name": "CHAMPI", "wind_kmh": 120, "time": "2026-11-04T01:00:00+00:00"}]

    def get_cyclones(self):
        return [{"name": "พายุสมมติ", "wind_kmh": 90, "time": "2026-11-04T02:00:00+00:00", "source": "GDACS"}]

    def get_waves(self):
        return [{"lat": 9.5, "lon": 101.0, "height_m": 3.2, "peak_time": "2026-11-04T03:00:00+00:00"}]

    def get_water_quality(self):
        return [{"name_th": "สถานีปากแม่น้ำ", "province_th": "สมุทรปราการ", "salinity_gl": 0.8,
                 "time": "2026-11-04T08:00:00+07:00", "agency_th": "คพ."}]

    def get_pm25_stations(self):
        return [{"name_th": "สถานีเชียงใหม่", "area_th": "เมืองเชียงใหม่", "pm25": 45.6,
                 "time": "2026-11-04T08:00:00+07:00"}]

    def get_fire_hotspots(self):
        return [{"area_code": "TH5001", "hotspots_24h": 7, "time": "2026-11-04T08:00:00+07:00"}]

    def get_dams(self):
        return [{"id": "1", "name_th": "ภูมิพล", "province_th": "ตาก", "date": "2026-11-04"}]

    def get_dam_history(self, dam_id, series="storage"):
        return {"series": [{"year": 2026, "values": [["2026-10-28", 40], ["2026-11-03", 50]]}],
                "source": "ThaiWater"}

    def get_media(self):
        return {"radar": [{"image": "https://example.test/radar.jpg", "time": "2026-11-04T08:00:00+07:00"}],
                "rain_forecast": {"thailand": {"days": [{"image": "https://example.test/rain.jpg", "time": "2026-11-04T07:00:00+07:00"}]}},
                "source": "ThaiWater (สสน.)"}

    def get_assessments(self, area_code):
        return []

    def get_all_assessments(self):
        return []

    def get_official_warnings(self):
        return []

    def get_sources(self):
        return {}


agent = HazardAgent(GAZ, DataTools())


@pytest.mark.parametrize("question,field,local", [
    ("เมืองเชียงใหม่ PM2.5 สูงสุดเท่าไหร่", "pm25", "TH50"),
    ("น้ำเค็มรุกเสนาไหม", "salinity_gl", "TH14"),
    ("ภาคเหนือ PM2.5 สถานีไหนสูงสุด", "pm25", "TH50"),
    ("ภาคกลางความเค็มสูงสุดเท่าไหร่", "salinity_gl", "TH14"),
])
def test_station_answers_filter_province_or_region_before_ranking(question, field, local):
    class ScopedTools(DataTools):
        def stations(self):
            return [
                {"name_th": "นอกพื้นที่", "province_code": "TH40", field: 99, "time": NOW.isoformat()},
                {"name_th": "ในพื้นที่", "province_code": local, field: 10, "time": NOW.isoformat()},
                {"name_th": "ไม่ทราบจังหวัด", field: 100, "time": NOW.isoformat()},
            ]
        get_pm25_stations = get_water_quality = stations

    answer = HazardAgent(GAZ, ScopedTools()).ask(question, NOW)
    assert [row["name_th"] for row in answer.evidence] == ["ในพื้นที่"]
    assert "นอกพื้นที่" not in answer.text and "ไม่ทราบจังหวัด" not in answer.text
    assert "จังหวัด" in answer.text or "ภาค" in answer.text


def test_pm25_area_text_resolves_province_and_discloses_province_scope():
    class ScopedTools(DataTools):
        def get_pm25_stations(self):
            return [
                {"name_th": "นอกพื้นที่", "area_th": "เสนา พระนครศรีอยุธยา", "pm25": 90, "time": NOW.isoformat()},
                {"name_th": "ในพื้นที่", "area_th": "เมืองเชียงใหม่, เชียงใหม่", "pm25": 10, "time": NOW.isoformat()},
            ]

    answer = HazardAgent(GAZ, ScopedTools()).ask("เมืองเชียงใหม่ PM2.5 สูงสุดเท่าไหร่", NOW)
    assert [row["name_th"] for row in answer.evidence] == ["ในพื้นที่"]
    assert "จังหวัดเชียงใหม่" in answer.text and "ระดับจังหวัด" in answer.text


def test_station_address_uses_admin_suffix_instead_of_road_name():
    gaz = Gazetteer([
        *GAZ.areas,
        Area("TH10", "province", "กรุงเทพมหานคร", "Bangkok", None, 13.7, 100.5, 1500),
        Area("TH1004", "amphoe", "บางรัก", "Bang Rak", "TH10", 13.7, 100.5, 6),
        Area("TH96", "province", "นราธิวาส", "Narathiwat", None, 6.4, 101.8, 4500),
    ])

    class RoadTools(DataTools):
        def get_pm25_stations(self):
            return [{"name_th": "สถานีในกรุงเทพ", "area_th": "ริมถนนนราธิวาส เขตบางรัก, กรุงเทพฯ", "pm25": 20, "time": NOW.isoformat()}]

    station_agent = HazardAgent(gaz, RoadTools())
    assert station_agent.ask("กรุงเทพ PM2.5 สถานีไหนสูงสุด", NOW).evidence
    assert not station_agent.ask("นราธิวาส PM2.5 สถานีไหนสูงสุด", NOW).evidence


@pytest.mark.parametrize("question", ["เสนา PM2.5 สถานีไหนสูงสุด", "ความเค็มจังหวัดเชียงใหม่สูงสุดเท่าไหร่"])
def test_no_station_in_requested_area_does_not_fall_back_to_country(question):
    answer = agent.ask(question, NOW)
    assert not answer.evidence
    assert "ไม่พบสถานี" in answer.text
    assert "จังหวัด" in answer.text


@pytest.mark.parametrize("word", ["เท่าไร", "เท่าไหร่"])
def test_hotspot_count_alternative_wording(word):
    answer = agent.ask(f"จุดความร้อนเชียงใหม่วันนี้{word}", NOW)
    assert answer.intent == "fire_hotspots"
    assert "7 จุด" in answer.text


@pytest.mark.parametrize("question,providers", [
    ("ตอนนี้มีพายุอะไรบ้าง", ("thaiwater_storms", "gdacs_cyclones")),
    ("แผ่นดินไหวล่าสุดเกิดที่ไหน", ("tmd_earthquakes", "usgs_earthquakes")),
])
@pytest.mark.parametrize("failed", [0, 1])
@pytest.mark.parametrize("empty", [True, False])
def test_partial_event_source_failure_is_disclosed(question, providers, failed, empty):
    class PartialTools(DataTools):
        def get_sources(self):
            return {name: {"ok": i != failed} for i, name in enumerate(providers)}

    tools = PartialTools()
    if empty:
        tools.get_earthquakes = tools.get_storms = tools.get_cyclones = lambda: []
    answer = HazardAgent(GAZ, tools).ask(question, NOW)
    assert "ขัดข้อง" in answer.text
    if empty:
        assert "ไม่พบรายการ" not in answer.text


def test_gdacs_wind_band_is_not_presented_as_exact_storm_wind():
    class CycloneTools(DataTools):
        def get_storms(self):
            return []

        def get_cyclones(self):
            return [{"name": "TEST", "wind_threshold_kmh": 119, "time": NOW.isoformat()}]

    answer = HazardAgent(GAZ, CycloneTools()).ask("ตอนนี้มีพายุอะไรบ้าง", NOW)
    assert "ขอบเขตลมตั้งแต่ 119 กม./ชม." in answer.text
    assert "รวมแนวคาดการณ์" in answer.text
    assert "อัปเดตข้อมูล" in answer.text


@pytest.mark.parametrize("question,intent", [
    ("อาหารเค็มทำให้ความดันสูงไหม", "out_of_scope"),
    ("เชียงใหม่มีจุดความร้อนเสี่ยงไฟป่าไหม", "risk"),
    ("เรดาร์ฝนเมืองเชียงใหม่ฝนจะตกไหม", "risk"),
    ("น้ำเค็มตอนนี้เท่าไหร่", "water_quality"),
    ("ขอลิงก์ภาพเรดาร์ฝนเชียงใหม่ได้ไหม", "media"),
])
def test_data_intents_do_not_swallow_unrelated_or_area_risk_questions(question, intent):
    assert understand(question, GAZ, NOW).intent == intent


@pytest.mark.parametrize("question,key,url", [
    ("ขอลิงก์ภาพพยากรณ์ฝนล่าสุด", "rain_forecast", "forecast.jpg"),
    ("ขอลิงก์ภาพพายุล่าสุด", "storm", "storm.jpg"),
    ("ขอลิงก์ภาพคลื่นล่าสุด", "wave", "wave.jpg"),
])
def test_requested_media_group_is_not_displaced_by_many_radars(question, key, url):
    class ManyMediaTools(DataTools):
        def get_media(self):
            return {"radar": [{"image": f"https://example.test/radar{i}.jpg"} for i in range(12)],
                    key: [{"image": f"https://example.test/{url}"}]}

    answer = HazardAgent(GAZ, ManyMediaTools()).ask(question, NOW)
    assert url in answer.text
    assert "radar0.jpg" not in answer.text


@pytest.mark.parametrize("word,series,label", [("ปล่อยน้ำ", "released", "ระบาย"), ("น้ำไหลเข้า", "inflow", "ไหลเข้า")])
def test_yesterday_dam_question_uses_requested_metric_and_date(word, series, label):
    class HistoryTools(DataTools):
        def get_dam_history(self, dam_id, requested_series="storage"):
            assert requested_series == series
            return {"series": [{"year": 2026, "values": [["2026-11-03", 12], ["2026-11-04", 99]]}]}

    answer = HazardAgent(GAZ, HistoryTools()).ask(f"เขื่อนภูมิพลเมื่อวาน{word}เท่าไหร่", NOW)
    assert label in answer.text and "12" in answer.text and "3/11/2569" in answer.text
    assert "99" not in answer.text


def test_latest_earthquake_question_returns_number_time_and_source():
    answer = agent.ask("แผ่นดินไหวล่าสุดเกิดที่ไหน", NOW)
    assert answer.intent == "earthquake"
    assert "4.2" in answer.text and "4/11/2569" in answer.text and "USGS" in answer.text


def test_storm_question_combines_hii_and_cyclone_records_with_sources():
    answer = agent.ask("ตอนนี้มีพายุอะไรบ้าง", NOW)
    assert answer.intent == "storms"
    assert "CHAMPI" in answer.text and "พายุสมมติ" in answer.text
    assert "สสน." in answer.text and "GDACS" in answer.text and "4/11/2569" in answer.text


def test_tropical_cyclone_wording_routes_to_active_storm_list():
    question = "ตอนนี้มีพายุหมุนอะไรบ้าง"
    assert understand(question, GAZ, NOW).intent == "storms"
    answer = agent.ask(question, NOW)
    assert "CHAMPI" in answer.text and "พายุสมมติ" in answer.text


def test_wave_question_returns_measured_forecast_value_time_and_source():
    answer = agent.ask("คลื่นสูงสุดวันนี้เท่าไหร่", NOW)
    assert answer.intent == "waves"
    assert "3.2 เมตร" in answer.text and "4/11/2569" in answer.text and "Open-Meteo Marine" in answer.text


def test_salinity_question_returns_station_value_time_and_source():
    answer = agent.ask("สถานีไหนมีความเค็มสูงสุด", NOW)
    assert answer.intent == "water_quality"
    assert "สถานีปากแม่น้ำ" in answer.text and "0.8" in answer.text
    assert "4/11/2569" in answer.text and "คพ." in answer.text


def test_pm25_station_question_includes_numeric_unit_time_and_source():
    answer = agent.ask("สถานี PM2.5 สูงสุดตอนนี้เท่าไหร่", NOW)
    assert answer.intent == "pm25_stations"
    assert "45.6 มคก./ลบ.ม." in answer.text and "4/11/2569" in answer.text and "Air4Thai" in answer.text


def test_fire_hotspot_question_aggregates_for_region_and_names_source():
    answer = agent.ask("ภาคเหนือมีจุดความร้อนไฟป่ากี่จุด", NOW)
    assert answer.intent == "fire_hotspots"
    assert "7 จุด" in answer.text and "ภาคเหนือ" in answer.text
    assert "4/11/2569" in answer.text and "NASA FIRMS" in answer.text


def test_fire_hotspot_question_filters_to_a_named_province():
    answer = agent.ask("จังหวัดเชียงใหม่มีจุดความร้อนกี่จุด", NOW)
    assert answer.intent == "fire_hotspots" and "เชียงใหม่ 7 จุด" in answer.text


def test_dam_history_compares_requested_week_from_the_records_when_the_llm_declines():
    answer = HazardAgent(GAZ, DataTools(), explainer=lambda q, f: None).ask("เขื่อนภูมิพลเทียบกับสัปดาห์ก่อนเป็นอย่างไร", NOW)
    assert answer.intent == "dam_history"
    assert "ภูมิพล" in answer.text and "50" in answer.text and "40" in answer.text
    assert "ThaiWater" in answer.text and "3/11/2569" in answer.text


def test_dam_history_with_no_record_through_today_reports_insufficient_data():
    class FutureOnlyTools(DataTools):
        def get_dam_history(self, dam_id, series="storage"):
            return {"series": [{"year": 2026, "values": [["2026-11-05", 50]]}]}

    answer = HazardAgent(GAZ, FutureOnlyTools()).ask("เขื่อนภูมิพลเทียบกับเมื่อวานเป็นอย่างไร", NOW)
    assert "ยังไม่ครบพอ" in answer.text


def test_media_question_returns_clickable_url_time_and_source():
    answer = agent.ask("ขอลิงก์ภาพเรดาร์ล่าสุด", NOW)
    assert answer.intent == "media"
    assert "https://example.test/radar.jpg" in answer.text
    assert "4/11/2569" in answer.text and "สสน." in answer.text


def test_media_question_extracts_nested_forecast_image_groups():
    answer = agent.ask("ขอลิงก์ภาพพยากรณ์ฝนล่าสุด", NOW)
    assert "https://example.test/rain.jpg" in answer.text


def test_data_request_intents_are_recognized_without_an_area():
    questions = {
        "แผ่นดินไหวล่าสุดเกิดที่ไหน": "earthquake",
        "ตอนนี้มีพายุอะไรบ้าง": "storms",
        "คลื่นสูงสุดวันนี้เท่าไหร่": "waves",
        "สถานีไหนมีความเค็มสูงสุด": "water_quality",
        "สถานี PM2.5 สูงสุดตอนนี้เท่าไหร่": "pm25_stations",
        "ภาคเหนือมีจุดความร้อนไฟป่ากี่จุด": "fire_hotspots",
        "เขื่อนภูมิพลเทียบกับสัปดาห์ก่อนเป็นอย่างไร": "dam_history",
        "ขอลิงก์ภาพเรดาร์ล่าสุด": "media",
    }
    assert {q: understand(q, GAZ, NOW).intent for q in questions} == questions


def test_wave_risk_question_with_an_area_stays_on_the_assessment_path():
    assert understand("พรุ่งนี้คลื่นสูงไหมที่อำเภอเมืองเชียงใหม่", GAZ, NOW).intent == "risk"


def test_unavailable_source_is_reported_instead_of_claiming_no_observations():
    class UnavailableTools(DataTools):
        def get_water_quality(self):
            return []

        def get_sources(self):
            return {"thaiwater_waterquality": {"ok": False}}

    answer = HazardAgent(GAZ, UnavailableTools()).ask("สถานีไหนมีความเค็มสูงสุด", NOW)
    assert "ขัดข้อง" in answer.text and "ยังตรวจสอบค่าความเค็มล่าสุดไม่ได้" in answer.text


class MonthOfQuakes(DataTools):
    def get_earthquakes(self):
        return [
            {"magnitude": 2.3, "place_th": "ต.ปง อ.ปง จ.พะเยา", "time": "2026-11-04T01:52:26+00:00", "lat": 19.13, "lon": 100.29,
             "source": "กรมอุตุนิยมวิทยา"},
            {"magnitude": 3.1, "place_th": "ประเทศเวียดนาม", "time": "2026-11-04T06:41:17+00:00", "lat": 15.0, "lon": 108.1,
             "source": "กรมอุตุนิยมวิทยา"},
            {"magnitude": 2.6, "place_th": "ต.ผาช้างน้อย อ.ปง จ.พะเยา", "time": "2026-11-01T03:10:00+00:00", "lat": 19.2, "lon": 100.4,
             "source": "กรมอุตุนิยมวิทยา"},
            {"magnitude": 2.0, "place_th": "อ.ปง จ.พะเยา", "time": "2026-10-20T03:10:00+00:00", "lat": 19.2, "lon": 100.4,
             "source": "กรมอุตุนิยมวิทยา"},
        ]


PHAYAO = Gazetteer([*GAZ.areas, Area("TH56", "province", "พะเยา", "Phayao", None, 19.2, 100.2, 6300)])


def test_quakes_this_month_in_a_province_lists_every_nearby_event_since_the_first():
    question = "พะเยาแผ่นดินไหวที่ผ่านมาในเดือนนี้"
    assert understand(question, PHAYAO, NOW).intent == "earthquake"
    answer = HazardAgent(PHAYAO, MonthOfQuakes()).ask(question, NOW)
    assert "ตั้งแต่ต้นเดือนนี้: 2 ครั้ง" in answer.text
    assert "1/11/2569" in answer.text and "4/11/2569" in answer.text
    assert "เวียดนาม" not in answer.text and "20/10/2569" not in answer.text  # far away / last month


def test_quake_count_question_with_mai_still_gets_the_list():
    answer = HazardAgent(PHAYAO, MonthOfQuakes()).ask("สัปดาห์นี้พะเยามีแผ่นดินไหวกี่ครั้ง", NOW)
    assert answer.intent == "earthquake" and "1 ครั้ง" in answer.text
    assert "4/11/2569" in answer.text and "1/11/2569" not in answer.text


def test_quakes_last_month_uses_previous_calendar_month():
    answer = HazardAgent(PHAYAO, MonthOfQuakes()).ask("พะเยาแผ่นดินไหวเดือนที่แล้วกี่ครั้ง", NOW)

    assert "1 ครั้ง" in answer.text
    assert "20/10/2569" in answer.text
    assert "1/11/2569" not in answer.text and "4/11/2569" not in answer.text
    # The kept TMD history starts on 20 Oct, after the month began: the count is partial, and the answer says so.
    assert "อย่างน้อย 1 ครั้ง" in answer.text
    assert "ข้อมูลยังไม่ครบช่วงที่ถาม: ข้อมูลกรมอุตุฯ ที่ทันภัยเก็บไว้เริ่มตั้งแต่ 10:10 น. วันที่ 20/10/2569" in answer.text


@pytest.mark.parametrize("question", [
    "พะเยาแผ่นดินไหวเดือนก่อนไหม",
    "Did Phayao have earthquakes last month?",
    "Phayao earthquakes last week",
    "Did Phayao have an earthquake yesterday?",
])
def test_every_period_the_answer_knows_turns_a_place_question_into_the_list(question):
    assert understand(question, PHAYAO, NOW).intent == "earthquake"


def test_quakes_month_before_uses_previous_calendar_month():
    answer = HazardAgent(PHAYAO, MonthOfQuakes()).ask("พะเยาแผ่นดินไหวเดือนก่อนไหม", NOW)
    assert answer.intent == "earthquake" and "อย่างน้อย 1 ครั้ง" in answer.text and "20/10/2569" in answer.text


def test_quake_count_is_exact_when_the_kept_history_covers_the_period():
    answer = HazardAgent(PHAYAO, MonthOfQuakes()).ask("พะเยาแผ่นดินไหวเดือนนี้กี่ครั้ง", NOW)
    assert "ตั้งแต่ต้นเดือนนี้: 2 ครั้ง" in answer.text
    assert "อย่างน้อย" not in answer.text and "ข้อมูลยังไม่ครบ" not in answer.text


def test_quake_count_is_partial_when_no_tmd_history_is_kept():
    class UsgsOnly(DataTools):
        def get_earthquakes(self):
            return [{"magnitude": 4.1, "place": "Myanmar", "time": "2026-11-03T03:10:00+00:00", "lat": 19.5, "lon": 100.0,
                     "source": "USGS"}]

    answer = HazardAgent(PHAYAO, UsgsOnly()).ask("พะเยาแผ่นดินไหวสัปดาห์นี้กี่ครั้ง", NOW)
    assert "อย่างน้อย 1 ครั้ง" in answer.text
    assert "ข้อมูลยังไม่ครบช่วงที่ถาม: ยังไม่มีข้อมูลกรมอุตุฯ ที่ทันภัยเก็บไว้" in answer.text


def test_quake_count_is_partial_when_the_period_overlaps_a_tmd_gap():
    class MonthWithGap(MonthOfQuakes):
        def get_quake_gaps(self):
            return [{"start": "2026-11-02T00:00:00+00:00", "end": "2026-11-03T00:00:00+00:00"}]

    answer = HazardAgent(PHAYAO, MonthWithGap()).ask("พะเยาแผ่นดินไหวเดือนนี้กี่ครั้ง", NOW)
    assert "อย่างน้อย 2 ครั้ง" in answer.text
    assert "ข้อมูลยังไม่ครบช่วงที่ถาม: ข้อมูลกรมอุตุฯ อาจขาดช่วง 07:00 น. วันที่ 2/11/2569" in answer.text
    # Last month does not overlap the gap: no gap note from it.
    assert "อาจขาดช่วง" not in HazardAgent(PHAYAO, MonthWithGap()).ask("พะเยาแผ่นดินไหวเดือนที่แล้วกี่ครั้ง", NOW).text
