import json

from decision_engine.explainer import validate
from training.generate_explainer_dataset import generate_single_example
from training.generate_intent_dataset import DEFAULT_AREAS, generate_intent_samples


def test_generate_intent_samples_format():
    samples = generate_intent_samples(DEFAULT_AREAS, count=20, seed=123)
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


def test_generate_explainer_samples_pass_validation():
    passed = 0
    for _ in range(20):
        item = generate_single_example("อำเภอเสนา", "พระนครศรีอยุธยา")
        if item is not None:
            question, facts, answer = item
            problems = validate(answer, facts, question)
            assert problems == []
            passed += 1
    assert passed > 0
