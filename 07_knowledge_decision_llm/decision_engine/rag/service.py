"""Disaster guidance service for integrating RAG results into agent and explainer prompts."""

from typing import Optional

from decision_engine.rag.models import DisasterPassage
from decision_engine.rag.retriever import DisasterRetriever

_GLOBAL_RETRIEVER: Optional[DisasterRetriever] = None


def get_default_retriever() -> DisasterRetriever:
    global _GLOBAL_RETRIEVER
    if _GLOBAL_RETRIEVER is None:
        _GLOBAL_RETRIEVER = DisasterRetriever()
    return _GLOBAL_RETRIEVER


def get_disaster_guidance(
    hazard: str,
    alert_level: Optional[str] = None,
    query: str = "",
    top_k: int = 2,
    include_team_tips: bool = True,
) -> list[DisasterPassage]:
    """Retrieve actionable official instructions for a specific hazard and alert level, plus team tips if present."""
    retriever = get_default_retriever()
    official = retriever.search(
        query=query,
        hazard=hazard,
        alert_level=alert_level,
        authority="ปภ.",
        top_k=top_k,
    )
    general_raw = retriever.search(
        query=query,
        hazard="all",
        alert_level=alert_level,
        authority="ปภ.",
        top_k=2,
    )
    general = [p for p in general_raw if p.applies_to_hazard(hazard)][:1]
    if include_team_tips:
        team = retriever.search(
            query=query,
            hazard=hazard,
            alert_level=alert_level,
            authority="ทีมทันภัย",
            top_k=1,
            min_score=0.01,
        )
        return official + general + team
    return official + general


def format_guidance_for_facts(passages: list[DisasterPassage]) -> str:
    """Format passages into concise bullet points with strict citations for inclusion in facts."""
    if not passages:
        return ""

    official_passages = [p for p in passages if p.authority != "ทีมทันภัย" and p.citation]
    team_passages = [p for p in passages if p.authority == "ทีมทันภัย" or not p.citation]

    lines = []
    if official_passages:
        authorities = " / ".join(dict.fromkeys(p.authority for p in official_passages))
        lines.append(f"คำแนะนำตามคู่มือราชการ ({authorities}):")
        for p in official_passages:
            citation_header = f"• {p.title} ({p.authority})"
            lines.append(citation_header)
            for step in p.procedure_steps[:3]:  # Top 3 actionable steps to keep concise
                lines.append(f"  - {step}")
            if p.citation:
                lines.append(f"  ที่มา: {p.citation}")

    if team_passages:
        lines.append("คำแนะนำเพิ่มเติม (ไม่ได้มาจากคู่มือราชการ):")
        for p in team_passages:
            for step in p.procedure_steps:
                lines.append(f"  - {step}")

    return "\n".join(lines)
