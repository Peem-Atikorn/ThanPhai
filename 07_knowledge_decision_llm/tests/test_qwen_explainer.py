from unittest.mock import MagicMock

import pytest

import urllib.error

from decision_engine.explainer import (
    ExplainerConfig,
    FallbackExplainer,
    GeminiExplainer,
    QwenConfig,
    QwenExplainer,
    create_explainer_from_env,
    validate,
)

FACTS = """อ.เสนา จ.พระนครศรีอยุธยา — สถานการณ์ล่าสุดจากข้อมูลตรวจวัด
• น้ำท่วม: วิกฤต — ระดับน้ำสูงกว่าตลิ่ง 2.11 ม.
• ฝนหนัก/พายุ: ปกติ — ฝนสะสม 24 ชม. สูงสุด 9.8 มม.
หากอยู่ในอันตราย โทร 1784 (ปภ.) · 1669 (เจ็บป่วยฉุกเฉิน) · 199 (ดับเพลิง) ทันที"""

GOOD_ANSWER = "อำเภอเสนาตอนนี้น้ำท่วมอยู่ในระดับวิกฤต น้ำสูงกว่าตลิ่ง 2.11 เมตร ส่วนฝนยังปกติ (9.8 มม.) หากอันตรายโทร 1784"


def test_qwen_config():
    cfg = QwenConfig()
    assert cfg.model == "thanphai-qwen"
    assert cfg.endpoint == "http://host.docker.internal:11434"
    assert cfg.timeout_ms == 15000
    assert cfg.temperature == 0.0


def test_qwen_config_from_env(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "gemini-3.5-flash-lite")  # must be ignored by Qwen
    monkeypatch.setenv("QWEN_MODEL", "qwen-custom:7b")
    monkeypatch.setenv("QWEN_ENDPOINT", "http://gpu-host:11434/")
    monkeypatch.setenv("QWEN_TIMEOUT_MS", "2500")

    cfg = QwenConfig.from_env()
    assert cfg.model == "qwen-custom:7b"
    assert cfg.endpoint == "http://gpu-host:11434"
    assert cfg.timeout_ms == 2500


def test_qwen_good_answer_passes():
    mock_requester = MagicMock(return_value=GOOD_ANSWER)
    explainer = QwenExplainer(config=QwenConfig(), requester=mock_requester)

    result = explainer("น้ำจะท่วมเสนาไหม", FACTS)
    assert result == GOOD_ANSWER
    mock_requester.assert_called_once_with("น้ำจะท่วมเสนาไหม", FACTS)


def test_qwen_unsafe_answer_is_rejected_returns_none():
    # Hallucinated number 5.5
    bad_answer = GOOD_ANSWER.replace("2.11", "5.5")
    mock_requester = MagicMock(return_value=bad_answer)
    explainer = QwenExplainer(config=QwenConfig(), requester=mock_requester)

    result = explainer("น้ำจะท่วมเสนาไหม", FACTS)
    assert result is None
    # Bad answer is rejected by validate
    assert any("numbers not in facts" in p for p in validate(bad_answer, FACTS))


def test_qwen_network_error_fails_gracefully_to_none():
    mock_requester = MagicMock(side_effect=TimeoutError("Request timed out"))
    explainer = QwenExplainer(config=QwenConfig(), requester=mock_requester)

    result = explainer("น้ำจะท่วมเสนาไหม", FACTS)
    assert result is None


def test_qwen_caching():
    mock_requester = MagicMock(return_value=GOOD_ANSWER)
    explainer = QwenExplainer(config=QwenConfig(), requester=mock_requester)

    res1 = explainer("น้ำจะท่วมเสนาไหม", FACTS)
    res2 = explainer("น้ำจะท่วมเสนาไหม", FACTS)
    assert res1 == GOOD_ANSWER
    assert res2 == GOOD_ANSWER
    assert mock_requester.call_count == 1


def test_qwen_concurrency_slots():
    mock_requester = MagicMock(return_value=GOOD_ANSWER)
    explainer = QwenExplainer(config=QwenConfig(), requester=mock_requester)

    # Acquire all slots to simulate heavy load
    for _ in range(explainer.MAX_CONCURRENT_CALLS):
        explainer._slots.acquire()

    assert explainer("คำถาม", FACTS) is None
    assert mock_requester.call_count == 0

    explainer._slots.release()
    assert explainer("คำถาม", FACTS) == GOOD_ANSWER
    assert mock_requester.call_count == 1


def test_create_explainer_from_env_qwen(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "qwen")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("QWEN_ENDPOINT", "http://127.0.0.1:11434")

    explainer = create_explainer_from_env()
    assert isinstance(explainer, QwenExplainer)
    assert explainer.config.model == "thanphai-qwen"


def test_create_explainer_from_env_none(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("QWEN_ENDPOINT", raising=False)

    explainer = create_explainer_from_env()
    assert explainer is None


def test_create_explainer_from_env_qwen_endpoint_without_provider_returns_none(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setenv("QWEN_ENDPOINT", "http://127.0.0.1:11434")

    explainer = create_explainer_from_env()
    assert explainer is None


class _FakeGemini(GeminiExplainer):
    def __init__(self, answer):
        super().__init__(client=None, config=ExplainerConfig())
        self.answer, self.calls = answer, 0

    def _call_model(self, question, facts):
        self.calls += 1
        return self.answer

    def _call_chat(self, question, system):
        self.calls += 1
        return "สวัสดีครับ"


def test_fallback_qwen_first():
    qwen = QwenExplainer(config=QwenConfig(), requester=MagicMock(return_value=GOOD_ANSWER))
    gemini = _FakeGemini(GOOD_ANSWER)
    chain = FallbackExplainer(qwen, gemini)
    assert chain("คำถาม", FACTS) == GOOD_ANSWER
    assert gemini.calls == 0
    assert chain.config.model == "thanphai-qwen"


def test_fallback_to_gemini_when_qwen_down_and_qwen_paused():
    requester = MagicMock(side_effect=urllib.error.URLError("refused"))
    qwen = QwenExplainer(config=QwenConfig(), requester=requester)
    gemini = _FakeGemini(GOOD_ANSWER)
    chain = FallbackExplainer(qwen, gemini)
    assert chain("คำถาม", FACTS) == GOOD_ANSWER
    assert chain.config.model == "gemini-3.5-flash-lite"
    # Qwen is skipped for a while, so the next question does not wait for its timeout.
    assert chain("คำถามอื่น", FACTS) == GOOD_ANSWER
    assert requester.call_count == 1


def test_fallback_to_gemini_when_qwen_answer_rejected():
    qwen = QwenExplainer(config=QwenConfig(), requester=MagicMock(return_value="น้ำท่วมระดับปกติ 99 ม."))
    chain = FallbackExplainer(qwen, _FakeGemini(GOOD_ANSWER))
    assert chain("คำถาม", FACTS) == GOOD_ANSWER


def test_fallback_both_fail_gives_template():
    qwen = QwenExplainer(config=QwenConfig(), requester=MagicMock(return_value="น้ำท่วมระดับปกติ 99 ม."))
    chain = FallbackExplainer(qwen, _FakeGemini("ระดับปกติ 123"))
    assert chain("คำถาม", FACTS) is None


def test_create_explainer_qwen_with_gemini_fallback(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "qwen")
    monkeypatch.setenv("GEMINI_API_KEY", "test-not-a-key")
    monkeypatch.delenv("LLM_FALLBACK", raising=False)
    explainer = create_explainer_from_env()
    assert isinstance(explainer, FallbackExplainer)
    assert [type(e) for e in explainer.explainers] == [QwenExplainer, GeminiExplainer]

    monkeypatch.setenv("LLM_FALLBACK", "none")
    assert isinstance(create_explainer_from_env(), QwenExplainer)


def test_an_answer_that_repeats_the_prompt_is_rejected():
    leaked = "ระดับน้ำของแต่ละจุดมาจากข้อเท็จจริงที่ให้มา ห้ามเปลี่ยน\n" + GOOD_ANSWER
    assert any("repeats its instructions" in p for p in validate(leaked, FACTS))
    assert not any("repeats" in p for p in validate(GOOD_ANSWER, FACTS))
