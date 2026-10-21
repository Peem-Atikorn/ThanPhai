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
    page_start: int
    page_end: Optional[int] = None
    procedure_steps: list[str] = field(default_factory=list)
    content: str = ""
    score: float = 0.0

    def full_text(self) -> str:
        steps = " ".join(self.procedure_steps) if self.procedure_steps else ""
        return f"{self.section} {self.content} {steps}".strip()

    def format_citation(self) -> str:
        if self.page_end and self.page_end != self.page_start:
            page_str = f" หน้า {self.page_start}–{self.page_end}"
        else:
            page_str = f" หน้า {self.page_start}"
        return f"[{self.authority} {self.title}{page_str}]"

    @property
    def citation(self) -> str:
        """Dynamic citation generated from authority, title, and page range."""
        return self.format_citation()

    @property
    def page(self) -> int:
        """Backwards-compatibility property returning page_start."""
        return self.page_start
