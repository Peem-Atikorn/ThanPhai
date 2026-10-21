"""Unit tests for Module 07 Disaster Knowledge & RAG Engine."""

import json
from pathlib import Path
import pytest
from decision_engine.rag.knowledge_data import DISASTER_KNOWLEDGE_BASE
from decision_engine.rag.models import DisasterPassage
from decision_engine.rag.retriever import DisasterRetriever, normalize_thai, tokenize_thai
from decision_engine.rag.service import (
    format_guidance_for_agent,
    format_guidance_for_facts,
    get_disaster_guidance,
)


def test_tokenize_thai():
    tokens = tokenize_thai("น้ำท่วมฉับพลันและน้ำป่าไหลหลาก")
    assert len(tokens) > 0
    assert any("น้ำ" in t or "ท่วม" in t for t in tokens)


def test_normalize_thai_handles_pdf_artifacts():
    # Normalizes decomposed vowels (tone mark + sara-aa, or nikhahit + sara-aa)
    assert normalize_thai("เตรียมน้้าดื่ม") == "เตรียมน้ำดื่ม"
    assert normalize_thai("นำ้ท่วม") == "น้ำท่วม"
    assert normalize_thai("น\u0e49\u0e4d\u0e32ท่วม") == "น้ำท่วม"
    assert normalize_thai("น\u0e4d\u0e49\u0e32ท่วม") == "น้ำท่วม"
    # BM25 tokenization matches normalized form
    tokens = tokenize_thai("น้้าท่วมฉับพลัน")
    assert any("น้ำ" in t for t in tokens)


def test_retriever_initialization():
    retriever = DisasterRetriever()
    assert len(retriever.passages) > 0
    assert retriever.bm25.avgdl > 0


def test_all_passages_have_real_page_and_source_url():
    retriever = DisasterRetriever()
    for p in retriever.passages:
        if p.authority == "ทีมทันภัย":
            # Team recommendations must not claim government sources
            assert p.source_url == "", f"{p.id} team passage should have empty source_url"
            assert p.citation == "", f"{p.id} team passage should have empty citation"
            assert p.pages == []
            continue

        # Official government publications must have valid URL and pages
        assert p.source_url.startswith("http://") or p.source_url.startswith("https://"), f"{p.id} missing url"
        assert p.pages and all(x > 0 for x in p.pages), f"{p.id} missing real pages"
        assert p.citation.startswith("[ปภ."), f"{p.id} citation format"
        assert "หน้า" in p.citation, f"{p.id} citation must include page number"
        assert "-p" not in p.id, f"{p.id} should use semantic topic ID rather than page suffix"

    # Specific check for gas shutoff passage (pages 26, 66)
    gas_p = next(p for p in retriever.passages if p.id == "ddpm-gas-valve-shutoff")
    assert gas_p.pages == [26, 66]
    assert gas_p.citation == "[ปภ. คู่มือประชาชนในการเตรียมตัวให้รอดปลอดภัยพิบัติ หน้า 26, 66]"


def test_passages_match_official_ddpm_source_pages():
    """Verify that every official passage in the knowledge base is grounded in the cited DDPM handbook pages."""
    ref_file = Path(__file__).parent / "data" / "ddpm_reference_pages.json"
    assert ref_file.exists(), "Reference pages file missing"

    with open(ref_file, encoding="utf-8") as f:
        ref_pages = json.load(f)

    expected_terms_by_passage = {
        "ddpm-flood-watch": ["ขึ้นลงของ", "กระสอบทราย", "สิ่งของมีค่า", "สะพานไม้", "กระชัง"],
        "ddpm-flood-warning": ["พยากรณ์อากาศ", "น้ำดื่ม", "กระสอบทราย", "ตัดกระแสไฟฟ้า", "ท่อระบายน้ำ"],
        "ddpm-flood-critical": ["อพยพ", "เด็กและคนชรา", "เหนือเข่า", "ห้ามขับรถ"],
        "ddpm-flood-health": ["จมน้ำ", "ไฟฟ้าดูด", "สารส้ม", "ฉี่หนู", "บู๊ท", "สัตว์มีพิษ"],
        "ddpm-storm-watch": ["สภาพบ้านเรือน", "จัดเก็บสิ่งของ", "ตัดแต่งกิ่งไม้", "ป้ายโฆษณา", "ค้ำยัน"],
        "ddpm-storm-warning": ["หลบในอาคาร", "ป้ายโฆษณา", "เครื่องประดับ", "นั่งยอง", "รถ"],
        "ddpm-emergency-kit": ["ถุงยังชีพ", "เครื่องอุปโภคบริโภค", "บัตร", "กระเป๋าเสื้อ"],
        "ddpm-gas-valve-shutoff": ["ปิดวาล์วถังก๊าซ", "ท่อแก๊ส", "ตัดกระแสไฟฟ้า", "ก๊าซรั่วไหล", "เปิดประตู"],
    }

    for p in DISASTER_KNOWLEDGE_BASE:
        if p.authority == "ทีมทันภัย":
            continue  # Team suggestions are verified separately

        combined_text = " ".join(ref_pages.get(str(pg), "") for pg in p.pages).replace("\n", "")
        assert combined_text, f"No reference text extracted for {p.id}"
        terms = expected_terms_by_passage.get(p.id, [])
        for term in terms:
            assert term in combined_text, f"Expected '{term}' in cited pages for {p.id}"


def test_team_recommendations_not_under_official_citation():
    """Verify that team tips have no citation and are cleanly separated from official government advice."""
    retriever = DisasterRetriever()
    team_passages = [p for p in retriever.passages if p.authority == "ทีมทันภัย"]
    assert len(team_passages) >= 2
    for p in team_passages:
        assert p.citation == ""
        assert p.source_url == ""

    # Test formatting in facts
    guidance = get_disaster_guidance(hazard="flood", alert_level="WARNING", top_k=2)
    facts_text = format_guidance_for_facts(guidance)
    assert "คำแนะนำตามคู่มือราชการ (ปภ. / สธ. / กรมอุตุฯ):" in facts_text
    assert "คำแนะนำเพิ่มเติม (ไม่ได้มาจากคู่มือราชการ):" in facts_text

    # Verify team tip is under the team header and not above the official citation
    parts = facts_text.split("คำแนะนำเพิ่มเติม (ไม่ได้มาจากคู่มือราชการ):")
    assert len(parts) == 2
    assert "ที่มา: [ปภ." in parts[0]
    assert "ชาร์จโทรศัพท์มือถือ" in parts[1]
    assert "ทีมทันภัย" not in parts[0]


def test_retriever_filter_by_hazard():
    retriever = DisasterRetriever()
    results = retriever.search(query="การเตรียมตัวรับน้ำท่วม", hazard="flood", top_k=3)
    assert len(results) > 0
    for r in results:
        assert r.hazard == "flood"
        if r.authority != "ทีมทันภัย":
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
    assert any(g.hazard == "rain_storm" for g in guidance)
    assert any(g.authority == "ทีมทันภัย" for g in guidance)

    # Drought guidance must NOT include gas valve shutoff
    drought_guidance = get_disaster_guidance(hazard="drought", alert_level="WARNING", top_k=2)
    assert not any(g.id == "ddpm-gas-valve-shutoff" for g in drought_guidance)
    assert not any("วาล์วก๊าซ" in step for g in drought_guidance for step in g.procedure_steps)

    # Fire guidance can include applicable all-hazard passages (emergency kit, gas valve)
    fire_guidance = get_disaster_guidance(hazard="fire", alert_level="WARNING", top_k=2)
    assert len(fire_guidance) > 0
    assert all(g.applies_to_hazard("fire") for g in fire_guidance)


def test_format_guidance_for_facts():
    guidance = get_disaster_guidance(hazard="flood", alert_level="WARNING", top_k=1)
    formatted = format_guidance_for_facts(guidance)
    assert "คำแนะนำตามคู่มือราชการ" in formatted
    assert "ที่มา:" in formatted
    assert "หน้า" in formatted
    assert "คำแนะนำเพิ่มเติม (ไม่ได้มาจากคู่มือราชการ):" in formatted


def test_format_guidance_for_agent():
    guidance = get_disaster_guidance(hazard="flood", alert_level="WARNING", top_k=1)
    steps = format_guidance_for_agent(guidance)
    assert isinstance(steps, list)
    assert len(steps) > 0
    assert any("น้ำ" in s or "ของมีค่า" in s for s in steps)
