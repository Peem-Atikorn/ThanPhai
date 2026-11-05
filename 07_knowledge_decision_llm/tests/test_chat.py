"""Small talk: friendly replies, but never a hazard level, evacuation advice or a made-up number."""
from decision_engine.explainer import CHAT_INSTRUCTION, QwenConfig, QwenExplainer, validate_chat


def qwen(reply):
    calls = []

    def chat(question, system):
        calls.append(system)
        return reply

    return QwenExplainer(QwenConfig(model="thanphai-qwen"), requester=lambda q, f: "", chat_requester=chat), calls


def test_friendly_reply_passes_and_the_prompt_names_the_real_model():
    explainer, calls = qwen("สวัสดีครับ น้องทันภัยพร้อมช่วยติดตามภัยแล้วครับ 🐘")
    assert explainer.chat("สวัสดี") == "สวัสดีครับ น้องทันภัยพร้อมช่วยติดตามภัยแล้วครับ 🐘"
    assert "thanphai-qwen" in calls[0] and "ไม่ใช่ ChatGPT, Gemini หรือ Claude" in calls[0]


def test_chat_reply_with_a_level_or_number_is_rejected():
    assert validate_chat("ตอนนี้กรุงเทพอยู่ระดับเตือนภัยครับ")
    assert validate_chat("ฝนจะตก 80 มม. ครับ")
    assert validate_chat("ควรอพยพทันทีครับ")
    assert qwen("น้ำจะท่วม 2 เมตรครับ")[0].chat("พรุ่งนี้น้ำท่วมไหม") is None


def test_chat_reply_that_states_any_level_is_rejected():
    # "ระดับปกติ" sounds reassuring, but only the rules may say an area is NORMAL.
    for reply in ("วันนี้ทุกอย่างอยู่ในระดับปกติครับ", "ตอนนี้อยู่ในเกณฑ์เฝ้าระวังครับ", "สถานะ: วิกฤต",
                  "Everything is NORMAL here", "Bangkok is on WATCH today"):
        assert "hazard level in chat" in validate_chat(reply), reply


def test_ordinary_words_that_contain_level_words_are_allowed():
    assert validate_chat("ผมเป็นผู้ช่วยในแอปเตือนภัยทันภัย วันนี้อากาศดีตามปกติไหมครับ") == []
    assert validate_chat("ผมคือ qwen2.5-7b ครับ", model="qwen2.5-7b") == []
    assert validate_chat("Life is back to normal, ask me about floods anytime") == []


def test_chat_is_cached_and_a_failure_returns_none():
    explainer, calls = qwen("สวัสดีครับ")
    explainer.chat("สวัสดี")
    explainer.chat("  สวัสดี ")
    assert len(calls) == 1

    def broken(question, system):
        raise TimeoutError("ollama down")

    failing = QwenExplainer(QwenConfig(), requester=lambda q, f: "", chat_requester=broken)
    assert failing.chat("สวัสดี") is None


def test_chat_prompt_forbids_hazard_facts():
    assert "ห้ามบอกสถานการณ์ภัย" in CHAT_INSTRUCTION and "{model}" in CHAT_INSTRUCTION


def test_chat_reply_that_echoes_its_instructions_is_rejected():
    leaked = "สวัสดีครับ\nเรื่องทั่วไปที่ไม่เกี่ยวกับภัย ตอบได้สั้นๆ แบบเพื่อนคุย แล้วบอกว่าเรื่องที่ถนัดที่สุดคือการติดตามภัย"
    assert "repeats its instructions" in validate_chat(leaked)


def test_rewrite_may_not_add_a_level_to_a_dam_or_river_answer():
    from decision_engine.explainer import validate

    facts = "เขื่อนภูมิพล ปริมาณน้ำกักเก็บ: 9,599 ล้าน ลบ.ม. ณ 4/11/2569\nที่มา: ThaiWater (สสน.)"
    assert validate("เขื่อนภูมิพลมีน้ำ 9,599 ล้าน ลบ.ม. ณ 4/11/2569 ครับ", facts) == []
    assert any("level not in facts" in p for p in validate("เขื่อนภูมิพลอยู่ในระดับปกติ 9,599 ล้าน ลบ.ม. ครับ", facts))


def test_reply_that_drifts_into_chinese_is_rejected():
    from decision_engine.explainer import validate

    assert "not Thai or English" in validate_chat("สวัสดีครับ 你想要尝试一些新的餐厅吗")
    assert "not Thai or English" in validate("น้ำท่วม 你好", "• น้ำท่วม: ปกติ")
