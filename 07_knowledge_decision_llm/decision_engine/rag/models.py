from dataclasses import dataclass, field
from typing import Optional


def format_page_ranges(pages: list[int]) -> str:
    """Format a list of page numbers into consecutive ranges (e.g., '10–11', '4–6', '26, 66')."""
    if not pages:
        return ""
    sorted_pages = sorted(set(pages))
    ranges = []
    start = sorted_pages[0]
    prev = sorted_pages[0]
    for p in sorted_pages[1:]:
        if p == prev + 1:
            prev = p
        else:
            if start == prev:
                ranges.append(str(start))
            else:
                ranges.append(f"{start}–{prev}")
            start = p
            prev = p
    if start == prev:
        ranges.append(str(start))
    else:
        ranges.append(f"{start}–{prev}")
    return ", ".join(ranges)


@dataclass
class DisasterPassage:
    """An authoritative disaster guidance passage with strict citation and source metadata."""

    id: str
    document_id: str
    authority: str  # e.g., "ปภ.", "ทีมทันภัย"
    hazard: str  # Primary hazard e.g. "flood", "rain_storm", "all"
    alert_levels: list[str]  # e.g., ["NORMAL", "WATCH", "WARNING", "CRITICAL"]
    title: str
    section: str
    source_url: str = ""
    pages: list[int] = field(default_factory=list)
    hazards: list[str] = field(default_factory=list)  # Applicable hazards e.g. ["flood", "rain_storm", "fire"]
    procedure_steps: list[str] = field(default_factory=list)
    content: str = ""
    score: float = 0.0

    def __post_init__(self):
        if not self.hazards:
            self.hazards = [self.hazard]

    def full_text(self) -> str:
        steps = " ".join(self.procedure_steps) if self.procedure_steps else ""
        return f"{self.section} {self.content} {steps}".strip()

    def format_citation(self) -> str:
        if not self.authority or self.authority == "ทีมทันภัย" or not self.title:
            return ""
        page_str = f" หน้า {format_page_ranges(self.pages)}" if self.pages else ""
        return f"[{self.authority} {self.title}{page_str}]"

    @property
    def citation(self) -> str:
        """Dynamic citation generated from authority, title, and page range."""
        return self.format_citation()

    @property
    def page(self) -> Optional[int]:
        """Backwards-compatibility property returning the first page."""
        return self.pages[0] if self.pages else None

    @property
    def page_start(self) -> Optional[int]:
        return min(self.pages) if self.pages else None

    @property
    def page_end(self) -> Optional[int]:
        return max(self.pages) if self.pages else None

    def applies_to_hazard(self, h: str) -> bool:
        if not h:
            return True
        h_lower = h.lower()
        if self.hazards:
            return "all" in [x.lower() for x in self.hazards] or h_lower in [x.lower() for x in self.hazards]
        return self.hazard.lower() == "all" or self.hazard.lower() == h_lower
