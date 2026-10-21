"""Unit tests for Module 07 Disaster Knowledge & RAG Engine."""

import pytest
from decision_engine.rag.models import DisasterPassage
from decision_engine.rag.retriever import DisasterRetriever, tokenize_thai
from decision_engine.rag.service import (
    format_guidance_for_agent,
    format_guidance_for_facts,
    get_disaster_guidance,
)


def test_tokenize_thai():
    tokens = tokenize_thai("น้ำท่วมฉับพลันและน้ำป่าไหลหลาก")
    assert len(tokens) > 0
    assert any("น้ำ" in t or "ท่วม" in t for t in tokens)


def test_retriever_initialization():
    retriever = DisasterRetriever()
    assert len(retriever.passages) > 0
    assert retriever.bm25.avgdl > 0


def test_all_passages_have_real_page_and_source_url():
    retriever = DisasterRetriever()
    for p in retriever.passages:
        assert p.source_url.startswith("http://") or p.source_url.startswith("https://"), f"{p.id} missing url"
        assert p.page is not None and p.page > 0, f"{p.id} missing real page"
        assert p.citation.startswith("[ปภ."), f"{p.id} citation format"


def test_retriever_filter_by_hazard():
    retriever = DisasterRetriever()
    results = retriever.search(query="การเตรียมตัวรับน้ำท่วม", hazard="flood", top_k=3)
    assert len(results) > 0
    for r in results:
        assert r.hazard == "flood"
        assert r.citation.startswith("[")


def test_retriever_filter_by_alert_level():
    retriever = DisasterRetriever()
    results = retriever.search(query="อพยพด่วน หนีภัยน้ำท่วม", hazard="flood", alert_level="CRITICAL", top_k=2)
    assert len(results) > 0
    assert "CRITICAL" in results[0].alert_levels
    assert any("อพยพ" in s for s in results[0].procedure_steps)


def test_retriever_storm_and_lightning():
    retriever = DisasterRetriever()
    results = retriever.search(query="ฟ้าผ่า พายุฝนฟ้าคะนอง หลบที่ไหน", hazard="rain_storm", top_k=2)
    assert len(results) > 0
    assert any("ฟ้าผ่า" in s or "อาคาร" in s for r in results for step in r.procedure_steps for s in [step])


def test_service_get_disaster_guidance():
    guidance = get_disaster_guidance(hazard="rain_storm", alert_level="WARNING", top_k=2)
    assert len(guidance) > 0
    assert guidance[0].hazard == "rain_storm"


def test_format_guidance_for_facts():
    guidance = get_disaster_guidance(hazard="flood", alert_level="WARNING", top_k=1)
    formatted = format_guidance_for_facts(guidance)
    assert "คำแนะนำตามคู่มือราชการ" in formatted
    assert "ที่มา:" in formatted
    assert "หน้า" in formatted


def test_format_guidance_for_agent():
    guidance = get_disaster_guidance(hazard="flood", alert_level="WARNING", top_k=1)
    steps = format_guidance_for_agent(guidance)
    assert isinstance(steps, list)
    assert len(steps) > 0
    assert any("น้ำ" in s or "ของมีค่า" in s for s in steps)
