"""Qwen local AI TMD Warning Auditor and Zero-Trust Explainer.

Validates official TMD weather warnings and produces safe, verified summaries
without tampering with deterministic alert levels or distorting statements.
"""

from dataclasses import dataclass, field
import json
import logging
import os
import re
from typing import Any, Callable

from .explainer import QwenConfig, _numbers, call_qwen_api

log = logging.getLogger("warning_explainer")

DEFAULT_STRONG_WORDS = ["หนักมาก", "หลายพื้นที่", "น้ำท่วมฉับพลัน", "น้ำป่าไหลหลาก"]
PROHIBITED_INTENSIFIERS = ["รุนแรงมาก", "เป็นบริเวณกว้าง"]
PROHIBITED_ADVICE_WORDS = ["อพยพ", "หลบภัย", "ศูนย์พักพิง", "กักตุน", "evacuat"]

LEVELS_TH = ("ปกติ", "เฝ้าระวัง", "เตือนภัย", "วิกฤต")
LEVELS_EN = {"ปกติ": "NORMAL", "เฝ้าระวัง": "WATCH", "เตือนภัย": "WARNING", "วิกฤต": "CRITICAL"}

REGIONS_COVERED = {
    "ประเทศไทยตอนบน": [
        "ประเทศไทยตอนบน",
        "ภาคเหนือ",
        "ภาคตะวันออกเฉียงเหนือ",
        "ภาคอีสาน",
        "ภาคกลาง",
        "กรุงเทพมหานครและปริมณฑล",
        "กรุงเทพฯและปริมณฑล",
        "กรุงเทพและปริมณฑล",
        "กทม.และปริมณฑล",
        "ภาคตะวันออก",
    ],
    "ภาคเหนือ": ["ภาคเหนือ"],
    "ภาคตะวันออกเฉียงเหนือ": ["ภาคตะวันออกเฉียงเหนือ", "ภาคอีสาน"],
    "ภาคกลาง": ["ภาคกลาง"],
    "กรุงเทพมหานครและปริมณฑล": [
        "กรุงเทพมหานครและปริมณฑล",
        "กรุงเทพฯและปริมณฑล",
        "กรุงเทพและปริมณฑล",
        "กทม.และปริมณฑล",
    ],
    "ภาคตะวันออก": ["ภาคตะวันออก"],
    "ภาคใต้": ["ภาคใต้", "ภาคใต้ฝั่งตะวันออก", "ภาคใต้ฝั่งตะวันตก"],
    "ภาคใต้ฝั่งตะวันออก": ["ภาคใต้ฝั่งตะวันออก"],
    "ภาคใต้ฝั่งตะวันตก": ["ภาคใต้ฝั่งตะวันตก"],
}

HAZARD_KEYWORDS = {
    "flood": ["น้ำท่วม", "น้ำท่วมฉับพลัน", "น้ำป่า", "น้ำป่าไหลหลาก"],
    "rain_storm": ["ฝน", "ฝนตก", "ฝนตกหนัก", "พายุ", "ฝนฟ้าคะนอง", "ลูกเห็บ", "ลมกระโชกแรง"],
}

# Clause delimiter splitting statements while handling Thai word boundaries and abbreviations
CLAUSE_SPLIT = re.compile(
    r"[\n;]|(?<![ก-๙])\.(?!\S)|(?=ส่วน(?!มาก|ใหญ่)|หลังจากนั้น|สำหรับ|ขอให้|อนึ่ง|\sและ(?:มี)?(?:ฝน|พายุ|คลื่น|ลม|น้ำ|อากาศ)\S*บริเวณ)"
)


@dataclass(frozen=True)
class Discrepancy:
    field: str
    expected: Any
    actual: Any
    message: str


@dataclass
class WarningAuditResult:
    matched: bool
    discrepancies: list[Discrepancy] = field(default_factory=list)
    qwen_extraction: dict = field(default_factory=dict)


@dataclass(frozen=True)
class WarningSummaryResult:
    summary_text: str
    attribution: str = "สรุปโดย AI จากประกาศกรมอุตุนิยมวิทยา · ดูประกาศฉบับจริง"
    valid: bool = True


def _normalize_ws(text: str) -> str:
    """Remove all whitespace for exact Thai phrase comparison (e.g. 'น้ำป่าไหลหลา ก' -> 'น้ำป่าไหลหลาก')."""
    return re.sub(r"\s+", "", text)


def _split_clauses(text: str) -> list[str]:
    """Split Thai warning text into statements."""
    return [c.strip() for c in CLAUSE_SPLIT.split(text) if c.strip()]


def _find_region_match(text: str) -> list[str]:
    """Find all regions mentioned in text, longest first."""
    found = []
    for r in sorted(REGIONS_COVERED.keys(), key=len, reverse=True):
        if r in text:
            found.append(r)
    return found


def _split_clauses_with_regions(text: str) -> list[tuple[list[str], str]]:
    """Split text into clauses and associate each with its active regions (carrying forward if advice/continuation)."""
    raw_clauses = _split_clauses(text)
    results = []
    current_regions = []
    for c in raw_clauses:
        regs = _find_region_match(c)
        if regs:
            current_regions = regs
        results.append((list(current_regions), c))
    return results


def _allowed_days_for_region(reg_name: str, by_region: list[dict]) -> set[str]:
    """Compute allowed calendar day numbers for a region based on by_region from/to dates."""
    allowed = set()
    for item in by_region:
        areas = item.get("areas", [])
        covers = False
        for area in areas:
            if area in REGIONS_COVERED and (reg_name == area or reg_name in REGIONS_COVERED[area]):
                covers = True
                break
            if area == reg_name:
                covers = True
                break
        if covers:
            from_str = item.get("from")
            to_str = item.get("to")
            if from_str and to_str:
                try:
                    f_day = int(from_str.split("-")[-1])
                    t_day = int(to_str.split("-")[-1])
                    for d in range(f_day, t_day + 1):
                        allowed.add(str(d))
                except (ValueError, IndexError):
                    pass
    return allowed


def _allowed_hazards_for_region(reg_name: str, by_region: list[dict]) -> set[str]:
    """Compute allowed hazard types for a region based on by_region."""
    allowed = set()
    for item in by_region:
        areas = item.get("areas", [])
        covers = False
        for area in areas:
            if area in REGIONS_COVERED and (reg_name == area or reg_name in REGIONS_COVERED[area]):
                covers = True
                break
            if area == reg_name:
                covers = True
                break
        if covers:
            for h in item.get("hazards", []):
                allowed.add(h)
    return allowed


def _region_original_has_word(reg_name: str, word: str, original_regional_clauses: list[tuple[list[str], str]]) -> bool:
    """Check if the original announcement text clause for a region contains the specific word (whitespace-insensitive)."""
    norm_word = _normalize_ws(word)
    for regs, clause in original_regional_clauses:
        matches = reg_name in regs or any(
            parent for parent, children in REGIONS_COVERED.items() if reg_name in children and parent in regs
        )
        if matches:
            if norm_word in _normalize_ws(clause):
                return True
    return False


def validate_warning_summary(
    summary: str,
    original_text: str,
    by_region: list[dict],
    strong_words: list[str] | None = None,
) -> list[str]:
    """Strict zero-trust validator for AI TMD warning summary against ground-truth facts."""
    problems: list[str] = []
    strong_words = strong_words or DEFAULT_STRONG_WORDS

    # 1. Rule 7: Do not wrap entire summary in quotation marks
    stripped = summary.strip()
    if (stripped.startswith('"') and stripped.endswith('"')) or (
        stripped.startswith("“") and stripped.endswith("”")
    ):
        problems.append("Summary must not be wrapped in quotation marks or quotes (ห้ามใส่เครื่องหมายคำพูด)")

    # 2. Rule 4: Number verification (all numbers must be in original announcement)
    summary_nums = _numbers(summary)
    orig_nums = _numbers(original_text)
    unknown_nums = summary_nums - orig_nums
    if unknown_nums:
        problems.append(f"Contains numbers not in facts: {sorted(unknown_nums)}")

    # 3. Rule 1: No unauthorized alert level words
    allowed_levels = {item.get("level") for item in by_region}
    if "CRITICAL" not in allowed_levels:
        if "วิกฤต" in summary or "CRITICAL" in summary:
            problems.append("Contains unauthorized alert level: วิกฤต")

    # 4. Rule 5: No unauthorized advice
    for advice in PROHIBITED_ADVICE_WORDS:
        if advice in summary and advice not in original_text:
            problems.append(f"Contains advice not in original announcement: '{advice}' (คำแนะนำ)")

    # 5. Rule 3: Unsupported extreme intensifiers
    for intensifier in PROHIBITED_INTENSIFIERS:
        if intensifier in summary and intensifier not in original_text:
            problems.append(f"Contains unsupported intensifier: '{intensifier}' (ความแรง)")

    # 6. Clause-by-clause regional validation (Rules 2 & 3)
    summary_clauses = _split_clauses(summary)
    original_regional_clauses = _split_clauses_with_regions(original_text)

    for s_clause in summary_clauses:
        matched_regions = _find_region_match(s_clause)
        if not matched_regions:
            continue

        for reg in matched_regions:
            # Check dates in this clause for reg
            allowed_days = _allowed_days_for_region(reg, by_region)
            if allowed_days:
                clause_days = set(re.findall(r"\b\d{1,2}\b", s_clause))
                day_candidates = {d for d in clause_days if 1 <= int(d) <= 31 and d not in ["2569", "2568"]}
                if "2 เมตร" in s_clause or "2เมตร" in s_clause:
                    day_candidates.discard("2")
                invalid_days = day_candidates - allowed_days
                if invalid_days:
                    problems.append(
                        f"Date mismatch for '{reg}': days {sorted(invalid_days)} outside allowed {sorted(allowed_days)}"
                    )

            # Check hazards in this clause for reg
            allowed_hazards = _allowed_hazards_for_region(reg, by_region)
            for h_type, kws in HAZARD_KEYWORDS.items():
                if any(kw in s_clause for kw in kws):
                    if h_type not in allowed_hazards:
                        problems.append(
                            f"Hazard '{h_type}' not allowed for region '{reg}' in by_region"
                        )

            # Check word strength (Rule 3)
            for sw in strong_words:
                if sw in s_clause:
                    if not _region_original_has_word(reg, sw, original_regional_clauses):
                        problems.append(
                            f"Word strength escalated for '{reg}': '{sw}' not present in original announcement (ความแรง)"
                        )

    return problems


class WarningAuditor:
    """Audits TMD warnings by having Qwen extract structured facts and cross-checking by_region."""

    SYSTEM_PROMPT = """คุณคือผู้ตรวจทานประกาศเตือนภัยกรมอุตุนิยมวิทยา
หน้าที่: อ่านประกาศและสกัดข้อมูล ภาค/จังหวัด วันที่ และภัย ออกมาเป็น JSON
รูปแบบ JSON ที่ต้องส่งกลับ:
{
  "items": [
    {
      "areas": ["ประเทศไทยตอนบน"],
      "from": "YYYY-MM-DD",
      "to": "YYYY-MM-DD",
      "hazards": ["rain_storm"]
    }
  ]
}
กฎ:
1. hazards ให้ระบุ "rain_storm" (ฝนฟ้าคะนอง, ลมกระโชกแรง, ฝนตกหนัก) หรือ "flood" (น้ำท่วม, น้ำป่า)
2. สกัดข้อมูลตามข้อความจริง ห้ามคาดเดา ห้ามสลับภาค
3. ตอบเฉพาะ JSON เท่านั้น ไม่มีคำอธิบายอื่น"""

    def __init__(
        self,
        requester: Callable[[str], str] | None = None,
        config: QwenConfig | None = None,
    ):
        self.requester = requester
        self.config = config or QwenConfig.from_env()

    def _call_qwen(self, prompt: str) -> str:
        if self.requester:
            return self.requester(prompt)

        return call_qwen_api(
            endpoint=self.config.endpoint,
            model=self.config.model,
            system_instruction=self.SYSTEM_PROMPT,
            user_prompt=prompt,
            temperature=0.0,
            max_tokens=512,
            timeout_ms=self.config.timeout_ms,
        )

    def audit(
        self,
        title: str,
        body: str,
        by_region: list[dict],
    ) -> WarningAuditResult:
        """Cross-examine Qwen extraction with by_region without ever altering warning levels."""
        provider = os.getenv("LLM_PROVIDER", "").lower()
        if provider != "qwen" and self.requester is None:
            return WarningAuditResult(matched=True, discrepancies=[])

        prompt = f"ประกาศ: {title}\n\nเนื้อหา:\n{body}\n\nจงตอบเป็น JSON:"
        try:
            raw_resp = self._call_qwen(prompt)
            raw_json = raw_resp.strip()
            if "```json" in raw_json:
                raw_json = raw_json.split("```json", 1)[1].split("```", 1)[0].strip()
            elif "```" in raw_json:
                raw_json = raw_json.split("```", 1)[1].split("```", 1)[0].strip()

            parsed = json.loads(raw_json)
            extracted_items = parsed.get("items", [])
        except Exception as exc:
            log.warning("WarningAuditor failed to run or parse Qwen response: %s", exc)
            return WarningAuditResult(
                matched=False,
                discrepancies=[Discrepancy("json_parse", "valid_json", str(exc), "Failed to parse JSON from Qwen")],
            )

        discrepancies: list[Discrepancy] = []

        for exp in by_region:
            exp_areas = exp.get("areas", [])
            exp_hazards = set(exp.get("hazards", []))
            exp_from = exp.get("from")
            exp_to = exp.get("to")

            found = False
            for act in extracted_items:
                act_areas = act.get("areas", [])
                area_match = any(a in exp_areas for a in act_areas) or any(
                    any(child in exp_areas for child in REGIONS_COVERED.get(a, [])) for a in act_areas
                )
                if area_match:
                    found = True
                    act_hazards = set(act.get("hazards", []))
                    if act_hazards != exp_hazards:
                        discrepancies.append(
                            Discrepancy(
                                field="hazards",
                                expected=list(exp_hazards),
                                actual=list(act_hazards),
                                message=f"Hazard mismatch for {exp_areas}: expected {exp_hazards}, got {act_hazards}",
                            )
                        )
                    act_from = act.get("from")
                    act_to = act.get("to")
                    if exp_from and act_from and exp_from != act_from:
                        discrepancies.append(
                            Discrepancy(
                                field="date_from",
                                expected=exp_from,
                                actual=act_from,
                                message=f"Start date mismatch for {exp_areas}: expected {exp_from}, got {act_from}",
                            )
                        )
                    if exp_to and act_to and exp_to != act_to:
                        discrepancies.append(
                            Discrepancy(
                                field="date_to",
                                expected=exp_to,
                                actual=act_to,
                                message=f"End date mismatch for {exp_areas}: expected {exp_to}, got {act_to}",
                            )
                        )

            if not found:
                discrepancies.append(
                    Discrepancy(
                        field="areas",
                        expected=exp_areas,
                        actual=None,
                        message=f"Missing expected region in extraction: {exp_areas}",
                    )
                )

        if discrepancies:
            log.warning("TMD warning audit discrepancy detected: %s", discrepancies)
            return WarningAuditResult(matched=False, discrepancies=discrepancies, qwen_extraction=parsed)

        return WarningAuditResult(matched=True, discrepancies=[], qwen_extraction=parsed)


class WarningExplainer:
    """Summarizes TMD warnings with Qwen under strict zero-trust validation."""

    SYSTEM_PROMPT = """คุณคือผู้ช่วยสรุปประกาศเตือนภัยกรมอุตุนิยมวิทยาของระบบ "ทันภัย"
หน้าที่: เรียบเรียงสรุปประกาศสั้นๆ 2-3 บรรทัด ให้อ่านง่าย
กฎเหล็ก:
1. ภาค วันที่ และภัย ต้องตรงกับประกาศทุกประการ ห้ามสลับ ห้ามเพิ่ม ห้ามตัด
2. ห้ามเปลี่ยนความแรงของถ้อยคำ ถ้ามี "บางแห่ง" ห้ามเขียนเป็น "หลายพื้นที่"
3. ห้ามเติมคำว่า "รุนแรงมาก" หรือระดับเตือนภัยเองเด็ดขาด
4. ตัวเลขทุกตัว (วันที่, คลื่น) ต้องมาจากประกาศเท่านั้น
5. คำแนะนำต้องมาจากประกาศเท่านั้น ห้ามแต่งการอพยพขึ้นมาเอง
6. ห้ามใส่เครื่องหมายคำพูดครอบสรุป"""

    def __init__(
        self,
        requester: Callable[[str], str] | None = None,
        config: QwenConfig | None = None,
    ):
        self.requester = requester
        self.config = config or QwenConfig.from_env()

    def _call_qwen(self, prompt: str) -> str:
        if self.requester:
            return self.requester(prompt)

        return call_qwen_api(
            endpoint=self.config.endpoint,
            model=self.config.model,
            system_instruction=self.SYSTEM_PROMPT,
            user_prompt=prompt,
            temperature=0.1,
            max_tokens=300,
            timeout_ms=self.config.timeout_ms,
        )

    def summarize(
        self,
        title: str,
        body: str,
        by_region: list[dict],
    ) -> WarningSummaryResult | None:
        """Generate verified AI summary for warning card; returns None on validation failure or error."""
        provider = os.getenv("LLM_PROVIDER", "").lower()
        if provider != "qwen" and self.requester is None:
            return None

        prompt = f"ประกาศ: {title}\n\nเนื้อหาทางการ:\n{body}\n\nจงเรียบเรียงสรุป 2-3 บรรทัด:"
        try:
            raw_summary = self._call_qwen(prompt).strip()
            full_context = f"{title} {body}"
            problems = validate_warning_summary(
                summary=raw_summary,
                original_text=full_context,
                by_region=by_region,
            )
            if problems:
                log.warning("Warning summary failed validation: %s", problems)
                return None

            return WarningSummaryResult(
                summary_text=raw_summary,
                attribution="สรุปโดย AI จากประกาศกรมอุตุนิยมวิทยา · ดูประกาศฉบับจริง",
                valid=True,
            )
        except Exception as exc:
            log.warning("WarningExplainer error during summarize: %s", exc)
            return None
