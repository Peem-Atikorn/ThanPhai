from types import SimpleNamespace

import pytest

from decision_engine.explainer import ExplainerConfig, GeminiExplainer, validate

FACTS = """อ.เสนา จ.พระนครศรีอยุธยา — สถานการณ์ล่าสุดจากข้อมูลตรวจวัด
• น้ำท่วม: วิกฤต — ระดับน้ำสูงกว่าตลิ่ง 2.11 ม.
• ฝนหนัก/พายุ: ปกติ — ฝนสะสม 24 ชม. สูงสุด 9.8 มม.
หากอยู่ในอันตราย โทร 1784 (ปภ.) · 1669 (เจ็บป่วยฉุกเฉิน) · 199 (ดับเพลิง) ทันที"""

GOOD = "อำเภอเสนาตอนนี้น้ำท่วมอยู่ในระดับวิกฤต น้ำสูงกว่าตลิ่ง 2.11 เมตร ส่วนฝนยังปกติ (9.8 มม.) หากอันตรายโทร 1784"


def test_good_answer_passes():
    assert validate(GOOD, FACTS) == []


@pytest.mark.parametrize(
    ("answer", "problem"),
    [
        (GOOD.replace("2.11", "3.5"), "numbers not in facts"),        # invented number
        (GOOD.replace("วิกฤต", "เตือนภัย"), "level for flood"),       # changed level
        ("น้ำท่วมระดับวิกฤต 2.11 ม. โทร 1784", "level for rain_storm"),  # dropped the rain level
        ("พรุ่งนี้ฝนจะตก 80 มม. น้ำท่วมวิกฤต ฝนหนักปกติ", "numbers not in facts"),  # invented forecast
        ("", "empty"),
        # Invented advice (the user asked the model to tell people to go somewhere).
        ("น้ำท่วมวิกฤต 2.11 ม. ฝนหนักปกติ 9.8 มม. ควรอพยพไปวัดเสนา", "advice not in facts"),
        ("Flood CRITICAL 2.11 m, heavy rain NORMAL 9.8 mm. Evacuate to the temple.", "advice not in facts"),
        # Levels swapped between hazards on separate lines.
        ("น้ำท่วม: ปกติ 2.11 ม.\nฝนหนัก: วิกฤต 9.8 มม.\n1784", "level for flood"),
        # A wrong extra level on the flood line, even though the right one is also there.
        ("น้ำท่วมวิกฤต หรืออาจแค่เฝ้าระวัง 2.11 ม.\nฝนหนักปกติ 9.8 มม. 1784", "level added for flood"),
    ],
)
def test_unsafe_answers_are_rejected(answer, problem):
    assert any(problem in p for p in validate(answer, FACTS))


def test_general_phrase_with_level_word_is_not_a_level():
    # Live false rejection 2026-10-19: "ระดับเตือนภัยตัดสินด้วยกฎ" is a general phrase, not a WARNING level.
    facts = FACTS + "\nระดับเตือนภัยตัดสินด้วยกฎที่กำหนดไว้ล่วงหน้า ไม่ได้ให้ AI ตัดสินเอง"
    answer = GOOD + "\nระดับเตือนภัยมาจากกฎของระบบ ไม่ใช่ AI"
    assert validate(answer, facts) == []
    # And the general phrase alone does not satisfy a missing hazard line.
    assert any("level for flood" in p for p in validate("ระดับเตือนภัยมาจากกฎ ฝนหนักปกติ 9.8 1784", facts))


def test_english_answer_with_capital_levels():
    answer = "Sena district: flood is CRITICAL (water 2.11 m above the bank). Heavy rain: NORMAL (9.8 mm). Call 1784."
    assert validate(answer, FACTS) == []
    assert any("level for flood" in p for p in validate(answer.replace("CRITICAL", "WARNING"), FACTS))


def test_english_no_forecast_notice():
    facts = FACTS + "\nหมายเหตุ: ทันภัยยังไม่มีพยากรณ์ล่วงหน้าสำหรับวันที่ 20/10"
    answer = "No forecast yet for 20/10. Now: flood CRITICAL 2.11 m, heavy rain NORMAL 9.8 mm. Call 1784."
    assert validate(answer, facts) == []


def test_no_forecast_notice_must_survive():
    facts = FACTS + "\nหมายเหตุ: ทันภัยยังไม่มีพยากรณ์ล่วงหน้าสำหรับวันที่ 20/10"
    assert "dropped the no-forecast notice" in validate(GOOD, facts)
    assert validate(GOOD + " ยังไม่มีพยากรณ์ของวันที่ 20/10", facts) == []


def test_level_without_hazard_name_is_not_enough():
    # "น้ำวิกฤต" does not name the hazard, so the flood level is treated as missing.
    assert any("level for flood" in p for p in validate("น้ำวิกฤต 2.11 ม. ฝนปกติ 9.8 มม. 1784", FACTS))


def test_shared_level_for_joined_hazards():
    facts = "• ฝนหนัก/พายุ: ปกติ — x\n• ไฟป่า/ไฟหญ้า: ปกติ — y"
    assert validate("• ฝนหนัก/พายุ และไฟป่า: ปกติ", facts) == []


def test_trailing_zero_numbers_match():
    assert validate("น้ำท่วมวิกฤต 2.110 ม. ฝนปกติ 9.80 มม. 1784", FACTS) == []


class FakeModels:
    def __init__(self, text=None, exc=None):
        self.text, self.exc, self.calls = text, exc, []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return SimpleNamespace(text=self.text)


def explainer(models):
    return GeminiExplainer(SimpleNamespace(models=models), ExplainerConfig(model="gemini-test"))


def test_call_sends_facts_system_instruction_and_model():
    models = FakeModels(GOOD)
    assert explainer(models)("น้ำจะท่วมเสนาไหม", FACTS) == GOOD
    call = models.calls[0]
    assert call["model"] == "gemini-test"
    assert "2.11" in call["contents"] and "น้ำจะท่วมเสนาไหม" in call["contents"]
    assert "ห้ามเปลี่ยน" in call["config"].system_instruction


def test_rejected_answer_returns_none():
    assert explainer(FakeModels(GOOD.replace("2.11", "4.0")))("q", FACTS) is None


def test_api_error_returns_none():
    from google.genai import errors

    exc = errors.ServerError(503, {"error": {"message": "overloaded", "status": "UNAVAILABLE"}})
    assert explainer(FakeModels(exc=exc))("q", FACTS) is None


def test_network_error_returns_none():
    assert explainer(FakeModels(exc=TimeoutError("timed out")))("q", FACTS) is None


def test_english_word_endings_count_as_hazard_names():
    # Live rejection 2026-10-19: "Flooding: NORMAL" was not recognised as the flood hazard.
    facts = "• น้ำท่วม: ปกติ — ระดับน้ำต่ำกว่าตลิ่ง 0.61 ม."
    assert validate("Hat Yai — Flooding: NORMAL (0.61 m below the bank)", facts) == []


def test_measured_numbers_from_the_question_are_not_allowed():
    # "น้ำสูง 3 เมตรแล้วใช่ไหม": the 3 is the user's claim, not a measurement; it must not come back as fact.
    facts = "• น้ำท่วม: วิกฤต — ระดับน้ำสูงกว่าตลิ่ง 2.08 ม."
    answer = "• น้ำท่วม: วิกฤต — สูงกว่าตลิ่ง 3 ม."
    assert any("numbers not in facts" in p for p in validate(answer, facts, question="น้ำสูง 3 เมตรแล้วใช่ไหมที่เสนา"))


def test_numbers_from_the_question_are_allowed():
    # Live rejection 2026-10-19: "3 วันข้างหน้า" came from the user, not from the model.
    facts = "• น้ำท่วม: เตือนภัย — ระดับน้ำสูงกว่าตลิ่ง 0.33 ม."
    answer = "ยังไม่มีข้อมูลล่วงหน้า 3 วัน • น้ำท่วม: เตือนภัย 0.33 ม."
    assert validate(answer, facts, question="3 วันข้างหน้าบางปะอินน้ำจะท่วมไหม") == []
    assert validate(answer, facts) != []


def test_buddhist_year_may_be_written_in_common_era():
    # Live rejection 2026-10-19: an English answer turned "2569" into "2026".
    facts = "• น้ำท่วม: ปกติ — 0.61 ม.\nประกาศ ฉบับที่ 4 (18-25 ตุลาคม 2569)"
    assert validate("Flooding: NORMAL (0.61 m). TMD notice no. 4, 18-25 Oct 2026.", facts) == []
    assert validate("Flooding: NORMAL (0.61 m). Notice from 2031.", facts) != []


FORECAST_FACTS = """อ.เสนา — สถานการณ์ล่าสุดจากข้อมูลตรวจวัด
พยากรณ์ฝนรายวันจากกรมอุตุนิยมวิทยา (แบบจำลอง WRF):
• พยากรณ์ฝน 20/10: เฝ้าระวัง — 48.0 มม. (ฝนตกหนัก)
• พยากรณ์ฝน 21/10: ปกติ — 2.0 มม.
• ฝนหนัก/พายุ: ปกติ — ฝนสะสม 24 ชม. สูงสุด 1.4 มม."""


def test_forecast_levels_are_checked_per_day():
    good = "พยากรณ์ฝน 20/10 เฝ้าระวัง (48.0 มม.) และ 21/10 ปกติ (2.0 มม.)\nฝนหนักตอนนี้ปกติ (1.4 มม.)"
    assert validate(good, FORECAST_FACTS) == []
    # Dropping the WATCH day, or calling it WARNING, is rejected.
    assert any("rain_forecast" in p for p in validate("พยากรณ์ฝน 21/10 ปกติ 2.0 มม.\nฝนหนักปกติ 1.4 มม.", FORECAST_FACTS))
    assert any("rain_forecast" in p for p in validate(good.replace("เฝ้าระวัง", "เตือนภัย"), FORECAST_FACTS))


def test_cache_avoids_a_second_call():
    models = FakeModels(GOOD)
    ex = explainer(models)
    assert ex("น้ำจะท่วมเสนาไหม", FACTS) == GOOD
    assert ex("  น้ำจะท่วมเสนาไหม ", FACTS) == GOOD
    assert len(models.calls) == 1
    # New facts (data refreshed) means a new call.
    ex("น้ำจะท่วมเสนาไหม", FACTS.replace("2.11", "2.08"))
    assert len(models.calls) == 2


def test_rate_limit_pauses_calls():
    from google.genai import errors

    models = FakeModels(exc=errors.ClientError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}}))
    ex = explainer(models)
    assert ex("q1", FACTS) is None
    assert ex("q2", FACTS) is None
    assert len(models.calls) == 1  # second question skipped the API during the pause
    ex._paused_until = 0  # pause over
    models.exc, models.text = None, GOOD
    assert ex("q3", FACTS) == GOOD


def test_busy_explainer_answers_with_template_instead_of_queueing():
    models = FakeModels(GOOD)
    ex = explainer(models)
    for _ in range(ex.MAX_CONCURRENT_CALLS):
        ex._slots.acquire()  # simulate calls in flight
    assert ex("q", FACTS) is None and models.calls == []
    ex._slots.release()
    assert ex("q", FACTS) == GOOD  # a slot is free again


def test_slot_is_released_after_errors():
    ex = explainer(FakeModels(exc=TimeoutError("slow")))
    for _ in range(ex.MAX_CONCURRENT_CALLS + 2):
        ex("q", FACTS)
    assert ex._slots.acquire(blocking=False)  # nothing leaked


def test_rejected_answers_are_not_cached():
    models = FakeModels(GOOD.replace("2.11", "4.0"))
    ex = explainer(models)
    ex("q", FACTS)
    ex("q", FACTS)
    assert len(models.calls) == 2


def test_no_key_means_no_explainer(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert GeminiExplainer.from_env() is None
