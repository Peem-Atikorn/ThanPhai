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


def test_hallucinated_number_fails_validation(sample_summary):
    # Says 50 km/h instead of 20
    bad_answer = "กลุ่มฝนกำลังเคลื่อนไปทางตะวันออกเฉียงใต้ ด้วยความเร็ว 50 กม./ชม. คาดว่าถึงบางนาใน 35 นาที"
    problems = validate_nowcast_explanation(bad_answer, sample_summary)
    assert any("numbers not in facts" in p for p in problems)


def test_hallucinated_direction_fails_validation(sample_summary):
    # Says North instead of Southeast
    bad_answer = "กลุ่มฝนกำลังเคลื่อนไปทางทิศเหนือ ด้วยความเร็ว 20 กม./ชม. คาดว่าถึงบางนาใน 35 นาที"
    problems = validate_nowcast_explanation(bad_answer, sample_summary)
    assert any("direction mismatch" in p for p in problems)


def test_hallucinated_district_fails_validation(sample_summary):
    # Mentions Pattaya which is not in target districts
    bad_answer = "กลุ่มฝนกำลังเคลื่อนไปทางตะวันออกเฉียงใต้ 20 กม./ชม. คาดว่าถึงพัทยาใน 35 นาที"
    problems = validate_nowcast_explanation(bad_answer, sample_summary)
    assert any("district" in p for p in problems)


def test_emergency_number_fails_validation(sample_summary):
    # Contains emergency phone number
    bad_answer = "กลุ่มฝนกำลังเคลื่อนตัว 20 กม./ชม. ไปทางตะวันออกเฉียงใต้ โทร 1784"
    problems = validate_nowcast_explanation(bad_answer, sample_summary)
    assert any("emergency" in p for p in problems)


def test_nowcast_explainer_uses_qwen_when_valid(sample_summary):
    good_answer = "ตรวจพบกลุ่มฝนปานกลางทางตะวันตกของกรุงเทพมหานคร กำลังเคลื่อนไปทางตะวันออกเฉียงใต้ ราว 20 กม./ชม. คาดว่าจะถึงบางนาใน 35 นาที"
    mock_requester = MagicMock(return_value=good_answer)
    explainer = NowcastExplainer(requester=mock_requester)

    result = explainer.explain(sample_summary)
    assert result == good_answer
    mock_requester.assert_called_once()


def test_nowcast_explainer_falls_back_to_template_on_rejection(sample_summary):
    # Mock returns answer with hallucinated speed (88 km/h)
    bad_answer = "กลุ่มฝนความเร็ว 88 กม./ชม. เคลื่อนไปทางตะวันออกเฉียงใต้"
    mock_requester = MagicMock(return_value=bad_answer)
    explainer = NowcastExplainer(requester=mock_requester)

    result = explainer.explain(sample_summary)
    # Must fallback to template answer
    assert "20" in result
    assert "88" not in result
    assert result == build_template_answer(sample_summary)


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
