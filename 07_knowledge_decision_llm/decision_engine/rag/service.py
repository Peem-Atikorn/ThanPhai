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
) -> list[DisasterPassage]:
    """Retrieve actionable official instructions for a specific hazard and alert level."""
    retriever = get_default_retriever()
    return retriever.search(query=query, hazard=hazard, alert_level=alert_level, top_k=top_k)


def format_guidance_for_facts(passages: list[DisasterPassage]) -> str:
    """Format passages into concise bullet points with strict citations for inclusion in facts."""
    if not passages:
        return ""

    lines = ["คำแนะนำตามคู่มือราชการ (ปภ. / สธ. / กรมอุตุฯ):"]
    for p in passages:
        citation_header = f"• {p.title} ({p.authority})"
        lines.append(citation_header)
        for step in p.procedure_steps[:3]:  # Top 3 actionable steps to keep concise
            lines.append(f"  - {step}")
        if p.citation:
            lines.append(f"  ที่มา: {p.citation}")
    return "\n".join(lines)


def format_guidance_for_agent(passages: list[DisasterPassage]) -> list[str]:
    """Extract plain action steps from retrieved passages for UI display."""
    steps = []
    for p in passages:
        for s in p.procedure_steps:
            if s not in steps:
                steps.append(s)
    return steps
