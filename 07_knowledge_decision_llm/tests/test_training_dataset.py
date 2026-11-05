import json
import random
import re
from collections import Counter

from decision_engine.explainer import validate
from geo_integration import Gazetteer
from geo_integration.gazetteer import Area
from hazard_agent.agent import _reason
from training.generate_explainer_dataset import (
    HAZARDS, LEVEL_KEY, _observations, generate_explainer_dataset, parse_facts, reason_en, split_areas,
)
from training.generate_intent_dataset import DEFAULT_AREAS, DEFAULT_PROVINCES, generate_intent_samples

GAZ = Gazetteer([
    Area("TH14", "province", "พระนครศรีอยุธยา", "Phra Nakhon Si Ayutthaya", None, 14.3, 100.5, 2500),
    Area("TH1412", "amphoe", "เสนา", "Sena", "TH14", 14.3, 100.4, 200),
    Area("TH1404", "amphoe", "บางบาล", "Bang Ban", "TH14", 14.4, 100.5, 180),
    Area("TH12", "province", "นนทบุรี", "Nonthaburi", None, 13.9, 100.5, 620),
    Area("TH1206", "amphoe", "ปากเกร็ด", "Pak Kret", "TH12", 13.9, 100.5, 90),
])
DISTRICTS = [a for a in GAZ.areas if a.level == "amphoe"]
THAI = re.compile(r"[฀-๿]")


def test_generate_intent_samples_format():
    samples = generate_intent_samples(DEFAULT_AREAS, DEFAULT_PROVINCES, count=50, seed=123)
    assert len(samples) >= 15
    for s in samples:
        assert "instruction" in s
        assert "input" in s
        assert "output" in s
        out = json.loads(s["output"])
        assert "intent" in out
        assert "hazards" in out
        assert "areas" in out
        assert "time_window" in out
        assert out["intent"] in ["risk", "explain", "prepare", "emergency", "dam", "river", "out_of_scope"]


def _split(sample: dict) -> tuple[str, str]:
    question, facts = sample["input"].split("\n\nข้อเท็จจริง (ห้ามเปลี่ยน):\n", 1)
    return question.removeprefix("คำถามผู้ใช้: "), facts


def test_explainer_samples_pass_validation_and_answer_in_the_question_language():
    stats = Counter()
    samples = generate_explainer_dataset(GAZ, DISTRICTS, 120, seed=1, stats=stats)
    assert len(samples) == 120
    for s in samples:
        question, facts = _split(s)
        assert validate(s["output"], facts, question) == []
        assert len(s["output"].splitlines()) <= 4
        if THAI.search(question):
            assert THAI.search(s["output"])
        else:
            assert not THAI.search(s["output"]), s["output"]
    # Generated answers are written to pass validate(); only rare edge cases may be dropped.
    assert sum(n for k, n in stats.items() if k.startswith("skip:level") or k.startswith("skip:numbers")) <= 3


def test_explainer_samples_cover_the_question_shapes_the_agent_sees():
    samples = generate_explainer_dataset(GAZ, DISTRICTS, 150, seed=2)
    pairs = [(*_split(s), s["output"]) for s in samples]
    hazard_lines = [len(re.findall(r"^• (?!พยากรณ์ฝน)", f, re.M)) for _, f, _ in pairs]
    assert any(n == 1 for n in hazard_lines)  # one hazard asked: the agent sends only that hazard
    assert any(n >= 8 for n in hazard_lines)  # overview: every hazard
    assert any(q.isascii() for q, _, _ in pairs) and any(not q.isascii() for q, _, _ in pairs)
    no_forecast = [(f, a) for _, f, a in pairs if "ยังไม่มีพยากรณ์" in f]
    assert no_forecast and all("ไม่มีพยากรณ์" in a or "no forecast" in a.lower() for _, a in no_forecast)
    assert any("พยากรณ์ฝน" in f for _, f, _ in pairs)
    assert len({q for q, _, _ in pairs}) > 40


def test_explainer_answers_leave_out_verbatim_lines():
    for s in generate_explainer_dataset(GAZ, DISTRICTS, 80, seed=3):
        assert "1784" not in s["output"] and "ที่มา" not in s["output"] and "⚠" not in s["output"]


def test_explainer_generation_is_repeatable():
    assert generate_explainer_dataset(GAZ, DISTRICTS, 20, seed=5) == generate_explainer_dataset(GAZ, DISTRICTS, 20, seed=5)


def test_test_districts_are_held_out_from_training():
    train, test = split_areas(GAZ, test_share=0.34, seed=42)
    assert test and train and not {a.code for a in train} & {a.code for a in test}
    assert {a.level for a in train + test} == {"amphoe"}


def test_english_reasons_keep_the_same_numbers_as_the_agent():
    rng = random.Random(0)
    for _ in range(300):
        hazard = rng.choice(HAZARDS)
        level = rng.choice(list(LEVEL_KEY))
        try:
            obs, rules = _observations(hazard, level, rng)
        except KeyError:
            continue  # this hazard has no such level
        thai = _reason({"observations": obs, "rule_ids": rules})
        assert sorted(re.findall(r"\d+(?:\.\d+)?", reason_en(obs, rules))) == sorted(re.findall(r"\d+(?:\.\d+)?", thai)), (hazard, obs)


def test_parse_facts_reads_the_agent_lines():
    facts = parse_facts(
        "อ.เสนา จ.พระนครศรีอยุธยา — สถานการณ์ล่าสุดจากข้อมูลตรวจวัด\n"
        "พยากรณ์ฝนรายวันจากกรมอุตุนิยมวิทยา (แบบจำลอง WRF):\n"
        "• พยากรณ์ฝน 5/11: เฝ้าระวัง — 42.0 มม. (ฝนหนัก)\n"
        "หมายเหตุ: ทันภัยยังไม่มีพยากรณ์ล่วงหน้าของน้ำท่วมสำหรับวันที่ 5/11 ข้อมูลด้านล่างคือสถานการณ์ ณ ตอนนี้\n"
        "• น้ำท่วม: วิกฤต — ระดับน้ำสูงกว่าตลิ่ง 2.11 ม.\n"
        "• ความร้อนจัด: ปกติ — ดัชนีความร้อนสูงสุดใน 24 ชม. ข้างหน้า 35.5°C (เกณฑ์กรมอุตุฯ: เตือนภัย)\n"
        "• ฝุ่น PM2.5: ไม่มีข้อมูลเพียงพอ\n"
        "• ภัยแล้ง: ยังไม่มีแบบจำลองหรือข้อมูลสำหรับพื้นที่นี้\n"
        "หากอยู่ในอันตราย โทร 1784 (ปภ.) · 1669 (เจ็บป่วยฉุกเฉิน) · 199 (ดับเพลิง) ทันที"
    )
    assert facts.label == "อ.เสนา จ.พระนครศรีอยุธยา"
    assert facts.forecast == [("5/11", "เฝ้าระวัง", "42.0")]
    assert facts.note == ("น้ำท่วม", "วันที่ 5/11")
    # The TMD heat band reuses level words for another scale, so it is not repeated in answers.
    assert facts.rated == [("flood", "วิกฤต", "ระดับน้ำสูงกว่าตลิ่ง 2.11 ม."),
                           ("heat", "ปกติ", "ดัชนีความร้อนสูงสุดใน 24 ชม. ข้างหน้า 35.5°C")]
    assert facts.no_data == ["air_quality"] and facts.no_model == ["drought"]
