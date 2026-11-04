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


def test_dam_history_compares_requested_week_and_never_calls_explainer():
    def must_not_run(question, facts):
        raise AssertionError("dam history must stay a direct data answer")

    answer = HazardAgent(GAZ, DataTools(), explainer=must_not_run).ask("เขื่อนภูมิพลเทียบกับสัปดาห์ก่อนเป็นอย่างไร", NOW)
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
