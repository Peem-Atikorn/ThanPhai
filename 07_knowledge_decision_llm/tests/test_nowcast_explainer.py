from unittest.mock import MagicMock
import pytest

from decision_engine.nowcast_explainer import (
    NowcastSummary,
    RainCellTarget,
    NowcastExplainer,
    build_template_answer,
    validate_nowcast_explanation,
)


@pytest.fixture
def sample_summary():
    return NowcastSummary(
        location_desc="ทางตะวันตกของกรุงเทพมหานคร",
        direction_th="ตะวันออกเฉียงใต้",
        speed_kmh=20.0,
        intensity_desc="ปานกลาง",
        targets=[
            RainCellTarget(amphoe_th="บางนา", eta_min=35),
            RainCellTarget(amphoe_th="พระโขนง", eta_min=25),
        ],
    )


def test_template_answer_passes_validation(sample_summary):
    template = build_template_answer(sample_summary)
    assert "20" in template
    assert "ตะวันออกเฉียงใต้" in template
    assert "บางนา" in template or "พระโขนง" in template
    problems = validate_nowcast_explanation(template, sample_summary)
    assert problems == []


def test_valid_qwen_answer_passes(sample_summary):
    answer = "กลุ่มฝนทางตะวันตกของกรุงเทพมหานคร กำลังเคลื่อนไปทางตะวันออกเฉียงใต้ ด้วยความเร็วราว 20 กม./ชม. คาดว่าจะถึงพระโขนงใน 25 นาที และบางนาใน 35 นาที"
    problems = validate_nowcast_explanation(answer, sample_summary)
    assert problems == []


def test_regex_catches_numbers_attached_to_thai_characters(sample_summary):
    # Says "ใน50นาที" without space; regex must extract 50 and reject it
    bad_answer = "กลุ่มฝนกำลังเคลื่อนไปทางตะวันออกเฉียงใต้ ด้วยความเร็ว 20 กม./ชม. คาดว่าถึงบางนาใน50นาที"
    problems = validate_nowcast_explanation(bad_answer, sample_summary)
    assert any("numbers not in facts" in p for p in problems)


def test_hallucinated_unconditional_time_bound_fails(sample_summary):
    # Says 60 when 60 is not in summary targets or speed
    bad_answer = "กลุ่มฝนความเร็ว 20 กม./ชม. เคลื่อนไปทางตะวันออกเฉียงใต้ คาดว่าถึงบางนาใน 60 นาที"
    problems = validate_nowcast_explanation(bad_answer, sample_summary)
    assert any("numbers not in facts" in p for p in problems)


def test_hallucinated_direction_fails_validation(sample_summary):
    bad_answer = "กลุ่มฝนกำลังเคลื่อนไปทางทิศเหนือ ด้วยความเร็ว 20 กม./ชม. คาดว่าถึงบางนาใน 35 นาที"
    problems = validate_nowcast_explanation(bad_answer, sample_summary)
    assert any("direction mismatch" in p for p in problems)


def test_hallucinated_district_with_gazetteer(sample_summary):
    class FakeArea:
        def __init__(self, name, level="amphoe"):
            self.name_th = name
            self.level = level

    class FakeGazetteer:
        areas = [
            FakeArea("บางนา"),
            FakeArea("พระโขนง"),
            FakeArea("บางพลี"),
            FakeArea("เมืองสมุทรปราการ"),
        ]

    # Mentions "บางพลี" which is in gazetteer but not in targets
    bad_answer = "กลุ่มฝนกำลังเคลื่อนไปทางตะวันออกเฉียงใต้ 20 กม./ชม. คาดว่าถึงบางพลีใน 35 นาที"
    problems = validate_nowcast_explanation(bad_answer, sample_summary, gazetteer=FakeGazetteer())
    assert any("unconfirmed district" in p for p in problems)


def test_emergency_number_fails_validation(sample_summary):
    bad_answer = "กลุ่มฝนกำลังเคลื่อนตัว 20 กม./ชม. ไปทางตะวันออกเฉียงใต้ โทร 1784"
    problems = validate_nowcast_explanation(bad_answer, sample_summary)
    assert any("emergency" in p for p in problems)


def test_nowcast_explainer_uses_qwen_when_valid(sample_summary):
    good_answer = "ตรวจพบกลุ่มฝนปานกลางทางตะวันตกของกรุงเทพมหานคร กำลังเคลื่อนไปทางตะวันออกเฉียงใต้ ราว 20 กม./ชม. คาดว่าจะถึงบางนาใน 35 นาที"
    mock_requester = MagicMock(return_value=good_answer)
    explainer = NowcastExplainer(requester=mock_requester)

    result, source = explainer.explain_with_source(sample_summary)
    assert result == good_answer
    assert source == "qwen"
    mock_requester.assert_called_once()


def test_nowcast_explainer_skips_qwen_if_provider_not_qwen(sample_summary, monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    # When requester is not injected and LLM_PROVIDER is not 'qwen', returns template without calling network
    explainer = NowcastExplainer(requester=None)
    result, source = explainer.explain_with_source(sample_summary)
    assert result == build_template_answer(sample_summary)
    assert source == "template"


def test_nowcast_explainer_empty_summary_returns_fallback():
    empty_summary = NowcastSummary(
        location_desc="",
        direction_th="ไม่มีกลุ่มฝน",
        speed_kmh=0.0,
        intensity_desc="ไม่มีฝน",
        targets=[],
    )
    explainer = NowcastExplainer(requester=None)
    result = explainer.explain(empty_summary)
    assert "ไม่พบกลุ่มฝนที่มีนัยสำคัญ" in result


def test_nowcast_explainer_undetermined_direction():
    undetermined_summary = NowcastSummary(
        location_desc="บริเวณจังหวัดเชียงใหม่",
        direction_th="ยังประเมินทิศทางไม่ได้",
        speed_kmh=0.0,
        intensity_desc="หนัก",
        targets=[],
    )
    explainer = NowcastExplainer(requester=None)
    result = explainer.explain(undetermined_summary)
    assert "ยังประเมินทิศทางไม่ได้" in result
    problems = validate_nowcast_explanation(result, undetermined_summary)
    assert problems == []


def test_nowcast_explainer_current_rainfall_eta_zero():
    summary = NowcastSummary(
        location_desc="บริเวณจังหวัดนครราชสีมา",
        direction_th="ตะวันตก",
        speed_kmh=15.0,
        intensity_desc="หนัก",
        targets=[
            RainCellTarget(amphoe_th="ลำสนธิ", eta_min=0),
            RainCellTarget(amphoe_th="เทพารักษ์", eta_min=20),
        ],
    )
    template = build_template_answer(summary)
    assert "ขณะนี้ฝนตกที่ลำสนธิ" in template
    assert "คาดว่าจะถึงเทพารักษ์ ใน 20 นาที" in template
    problems = validate_nowcast_explanation(template, summary)
    assert problems == []


