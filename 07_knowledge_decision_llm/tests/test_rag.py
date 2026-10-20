"""Unit tests for Module 07 Disaster Knowledge & Hybrid RAG Engine."""

import pytest
from decision_engine.rag.models import DisasterPassage
from decision_engine.rag.retriever import HybridDisasterRetriever, tokenize_thai
from decision_engine.rag.service import (
    format_guidance_for_agent,
    format_guidance_for_facts,
    get_disaster_guidance,
)


def test_tokenize_thai():
    tokens = tokenize_thai("น้ำท่วมฉับพลันและน้ำป่าไหลหลาก")
    assert len(tokens) > 0
    # Should include key words
    assert any("น้ำ" in t or "ท่วม" in t for t in tokens)


def test_retriever_initialization():
    retriever = HybridDisasterRetriever()
    assert len(retriever.passages) > 0
    assert retriever.bm25.avgdl > 0


def test_retriever_filter_by_hazard():
    retriever = HybridDisasterRetriever()
    # Search for flood
    results = retriever.search(query="การเตรียมตัวรับน้ำท่วม", hazard="flood", top_k=3)
    assert len(results) > 0
    for r in results:
        assert r.hazard == "flood"
        assert r.citation.startswith("[")


def test_retriever_filter_by_alert_level():
    retriever = HybridDisasterRetriever()
    results = retriever.search(query="อพยพด่วน", hazard="flood", alert_level="CRITICAL", top_k=2)
    assert len(results) > 0
    # CRITICAL passage should rank higher or match
    assert "CRITICAL" in results[0].alert_levels


def test_retriever_fire_hazard_and_pm25():
    retriever = HybridDisasterRetriever()
    results = retriever.search(query="ควันไฟป่าและฝุ่น PM2.5 N95", hazard="fire", top_k=2)
    assert len(results) > 0
    assert any("N95" in step or "หน้ากาก" in step for r in results for step in r.procedure_steps)


def test_service_get_disaster_guidance():
    guidance = get_disaster_guidance(hazard="rain_storm", alert_level="WARNING", top_k=2)
    assert len(guidance) > 0
    assert guidance[0].hazard == "rain_storm"


def test_format_guidance_for_facts():
    guidance = get_disaster_guidance(hazard="flood", alert_level="WARNING", top_k=1)
    formatted = format_guidance_for_facts(guidance)
    assert "คำแนะนำตามคู่มือราชการ" in formatted
    assert "ที่มา:" in formatted


def test_format_guidance_for_agent():
    guidance = get_disaster_guidance(hazard="drought", alert_level="WARNING", top_k=1)
    steps = format_guidance_for_agent(guidance)
    assert isinstance(steps, list)
    assert len(steps) > 0
    assert any("น้ำ" in s for s in steps)
