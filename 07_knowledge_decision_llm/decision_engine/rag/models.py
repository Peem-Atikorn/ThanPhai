from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DisasterPassage:
    """An authoritative disaster guidance passage with strict citation metadata."""

    id: str
    document_id: str
    authority: str  # e.g., "ปภ.", "กรมอุตุนิยมวิทยา", "กระทรวงสาธารณสุข", "กรมอุทยานฯ"
    hazard: str  # "flood", "rain_storm", "drought", "fire"
    alert_levels: list[str]  # e.g., ["NORMAL", "WATCH", "WARNING", "CRITICAL"]
    title: str
    section: str
    procedure_steps: list[str] = field(default_factory=list)
    content: str = ""
    citation: str = ""
    score: float = 0.0

    def full_text(self) -> str:
        steps = "\n".join(f"- {s}" for s in self.procedure_steps) if self.procedure_steps else ""
        return f"{self.title}\n{self.section}\n{self.content}\n{steps}".strip()

    def format_citation(self) -> str:
        if self.citation:
            return self.citation
        return f"[{self.authority} - {self.title} ({self.section})]"
