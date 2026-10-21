from dataclasses import dataclass, field
from typing import Optional


@dataclass
class DisasterPassage:
    """An authoritative disaster guidance passage with strict citation and source metadata."""

    id: str
    document_id: str
    authority: str  # e.g., "ปภ.", "กรมอุตุนิยมวิทยา", "กระทรวงสาธารณสุข", "กรมอุทยานฯ"
    hazard: str  # "flood", "rain_storm", "drought", "fire"
    alert_levels: list[str]  # e.g., ["NORMAL", "WATCH", "WARNING", "CRITICAL"]
    title: str
    section: str
    source_url: str
    page: Optional[int] = None
    procedure_steps: list[str] = field(default_factory=list)
    content: str = ""
    citation: str = ""
    score: float = 0.0

    def full_text(self) -> str:
        steps = " ".join(self.procedure_steps) if self.procedure_steps else ""
        return f"{self.section} {self.content} {steps}".strip()

    def format_citation(self) -> str:
        if self.citation:
            return self.citation
        page_str = f" หน้า {self.page}" if self.page else ""
        return f"[{self.authority} {self.title}{page_str}]"
