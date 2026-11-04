"""Qwen local AI TMD Warning Auditor and Zero-Trust Explainer.

Validates official TMD weather warnings and produces safe, verified summaries
without tampering with deterministic alert levels or distorting statements.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
import json
import logging
import os
from pathlib import Path
import re
from typing import Any, Callable

from .explainer import QwenConfig, _numbers, call_qwen_api

log = logging.getLogger("warning_explainer")

PROHIBITED_INTENSIFIERS = ["รุนแรงมาก", "เป็นบริเวณกว้าง"]
PROHIBITED_ADVICE_WORDS = ["อพยพ", "หลบภัย", "ศูนย์พักพิง", "กักตุน", "evacuat"]

LEVELS_TH = ("ปกติ", "เฝ้าระวัง", "เตือนภัย", "วิกฤต")
LEVELS_EN = {"ปกติ": "NORMAL", "เฝ้าระวัง": "WATCH", "เตือนภัย": "WARNING", "วิกฤต": "CRITICAL"}

# 77 Thai provinces: ISO 3166-2:TH code <-> Thai province name
PROVINCE_CODE_TO_NAME = {
    "TH10": "กรุงเทพมหานคร", "TH11": "สมุทรปราการ", "TH12": "นนทบุรี", "TH13": "ปทุมธานี",
    "TH14": "พระนครศรีอยุธยา", "TH15": "อ่างทอง", "TH16": "ลพบุรี", "TH17": "สิงห์บุรี",
    "TH18": "ชัยนาท", "TH19": "สระบุรี", "TH20": "ชลบุรี", "TH21": "ระยอง",
    "TH22": "จันทบุรี", "TH23": "ตราด", "TH24": "ฉะเชิงเทรา", "TH25": "ปราจีนบุรี",
    "TH26": "นครนายก", "TH27": "สระแก้ว", "TH30": "นครราชสีมา", "TH31": "บุรีรัมย์",
    "TH32": "สุรินทร์", "TH33": "ศรีสะเกษ", "TH34": "อุบลราชธานี", "TH35": "ยโสธร",
    "TH36": "ชัยภูมิ", "TH37": "อำนาจเจริญ", "TH38": "บึงกาฬ", "TH39": "หนองบัวลำภู",
    "TH40": "ขอนแก่น", "TH41": "อุดรธานี", "TH42": "เลย", "TH43": "หนองคาย",
    "TH44": "มหาสารคาม", "TH45": "ร้อยเอ็ด", "TH46": "กาฬสินธุ์", "TH47": "สกลนคร",
    "TH48": "นครพนม", "TH49": "มุกดาหาร", "TH50": "เชียงใหม่", "TH51": "ลำพูน",
    "TH52": "ลำปาง", "TH53": "อุตรดิตถ์", "TH54": "แพร่", "TH55": "น่าน",
    "TH56": "พะเยา", "TH57": "เชียงราย", "TH58": "แม่ฮ่องสอน", "TH60": "นครสวรรค์",
    "TH61": "อุทัยธานี", "TH62": "กำแพงเพชร", "TH63": "ตาก", "TH64": "สุโขทัย",
    "TH65": "พิษณุโลก", "TH66": "พิจิตร", "TH67": "เพชรบูรณ์", "TH70": "ราชบุรี",
    "TH71": "กาญจนบุรี", "TH72": "สุพรรณบุรี", "TH73": "นครปฐม", "TH74": "สมุทรสาคร",
    "TH75": "สมุทรสงคราม", "TH76": "เพชรบุรี", "TH77": "ประจวบคีรีขันธ์", "TH80": "นครศรีธรรมราช",
    "TH81": "กระบี่", "TH82": "พังงา", "TH83": "ภูเก็ต", "TH84": "สุราษฎร์ธานี",
    "TH85": "ระนอง", "TH86": "ชุมพร", "TH90": "สงขลา", "TH91": "สตูล",
    "TH92": "ตรัง", "TH93": "พัทลุง", "TH94": "ปัตตานี", "TH95": "ยะลา",
    "TH96": "นราธิวาส",
}
PROVINCE_NAME_TO_CODE = {name: code for code, name in PROVINCE_CODE_TO_NAME.items()}

# Load official TMD meteorological regions from geo_integration if available
try:
    from geo_integration.warning_areas import REGIONS as _TMD_REGIONS  # type: ignore

    PROVINCES_BY_REGION = {
        "ภาคเหนือ": list(_TMD_REGIONS["ภาคเหนือ"]),
        "ภาคตะวันออกเฉียงเหนือ": list(_TMD_REGIONS["ภาคตะวันออกเฉียงเหนือ"]),
        "ภาคกลาง": list(_TMD_REGIONS["ภาคกลาง"]),
        "กรุงเทพมหานครและปริมณฑล": list(_TMD_REGIONS["กรุงเทพมหานครและปริมณฑล"]) + ["กทม", "กรุงเทพฯ", "กรุงเทพ"],
        "ภาคตะวันออก": list(_TMD_REGIONS["ภาคตะวันออก"]),
        "ภาคใต้": list(_TMD_REGIONS["ภาคใต้"]),
    }
except Exception:
    PROVINCES_BY_REGION = {
        "ภาคเหนือ": [
            "เชียงราย", "แม่ฮ่องสอน", "เชียงใหม่", "ลำพูน", "ลำปาง", "พะเยา", "แพร่", "น่าน", "อุตรดิตถ์", "ตาก",
            "สุโขทัย", "พิษณุโลก", "พิจิตร", "กำแพงเพชร", "เพชรบูรณ์", "นครสวรรค์", "อุทัยธานี"
        ],
        "ภาคตะวันออกเฉียงเหนือ": [
            "เลย", "หนองคาย", "บึงกาฬ", "หนองบัวลำภู", "อุดรธานี", "สกลนคร", "นครพนม", "มุกดาหาร", "ขอนแก่น",
            "กาฬสินธุ์", "มหาสารคาม", "ร้อยเอ็ด", "ชัยภูมิ", "นครราชสีมา", "บุรีรัมย์", "สุรินทร์", "ศรีสะเกษ",
            "ยโสธร", "อำนาจเจริญ", "อุบลราชธานี"
        ],
        "ภาคกลาง": [
            "ชัยนาท", "สิงห์บุรี", "อ่างทอง", "ลพบุรี", "สระบุรี", "พระนครศรีอยุธยา", "สุพรรณบุรี", "กาญจนบุรี",
            "ราชบุรี", "สมุทรสงคราม"
        ],
        "กรุงเทพมหานครและปริมณฑล": [
            "กรุงเทพมหานคร", "กรุงเทพฯ", "กทม", "กรุงเทพ", "นนทบุรี", "ปทุมธานี", "สมุทรปราการ", "สมุทรสาคร", "นครปฐม"
        ],
        "ภาคตะวันออก": [
            "นครนายก", "ปราจีนบุรี", "สระแก้ว", "ฉะเชิงเทรา", "ชลบุรี", "ระยอง", "จันทบุรี", "ตราด"
        ],
        "ภาคใต้": [
            "เพชรบุรี", "ประจวบคีรีขันธ์", "ชุมพร", "สุราษฎร์ธานี", "นครศรีธรรมราช", "พัทลุง", "สงขลา",
            "ปัตตานี", "ยะลา", "นราธิวาส", "ระนอง", "พังงา", "ภูเก็ต", "กระบี่", "ตรัง", "สตูล"
        ],
    }

PROVINCE_TO_REGION = {p: r for r, plist in PROVINCES_BY_REGION.items() for p in plist}

# Short/ambiguous province names that can be common words in Thai
AMBIGUOUS_PROVINCES = {"เลย", "แพร่", "ตาก"}
PROVINCE_BEFORE = re.compile(r"(?:^|[\s,:;(]|จังหวัด|จ\.|และ)$")
PROVINCE_AFTER = re.compile(r"^(?:$|[\s,:;).]|และ)")

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
    summary_text: str | None = None
    attribution: str = "สรุปโดย AI จากประกาศกรมอุตุนิยมวิทยา · ดูประกาศฉบับจริง"
    valid: bool = True
    error: str | None = None


def load_strong_words() -> list[str]:
    """Read strong_words from decision_engine/rules.yaml to prevent divergence."""
    try:
        import yaml

        rules_path = Path(__file__).with_name("rules.yaml")
        if rules_path.exists():
            with rules_path.open(encoding="utf-8") as f:
                data = yaml.safe_load(f)
                words = data.get("official_warning", {}).get("strong_words")
                if words:
                    return list(words)
    except Exception:
        pass
    return ["หนักมาก", "หลายพื้นที่", "น้ำท่วมฉับพลัน", "น้ำป่าไหลหลาก"]


def _normalize_ws(text: str) -> str:
    """Remove all whitespace for exact Thai phrase comparison (e.g. 'น้ำป่าไหลหลา ก' -> 'น้ำป่าไหลหลาก')."""
    return re.sub(r"\s+", "", text)


def _split_clauses(text: str) -> list[str]:
    """Split Thai warning text into statements."""
    return [c.strip() for c in CLAUSE_SPLIT.split(text) if c.strip()]


def _find_region_match(text: str) -> tuple[list[str], list[str]]:
    """Find all regions and provinces mentioned in text.

    Returns (matched_regions, matched_provinces).
    """
    found_regions = []
    for r in sorted(REGIONS_COVERED.keys(), key=len, reverse=True):
        if r in text:
            found_regions.append(r)

    found_provinces = []
    for p, r in sorted(PROVINCE_TO_REGION.items(), key=lambda x: len(x[0]), reverse=True):
        if p in text:
            if p in AMBIGUOUS_PROVINCES:
                # Require province prefix (จ./จังหวัด) or whitespace/list boundary
                matched = False
                for m in re.finditer(re.escape(p), text):
                    before_ok = bool(PROVINCE_BEFORE.search(text[:m.start()]))
                    after_ok = bool(PROVINCE_AFTER.match(text[m.end():]))
                    if before_ok and after_ok:
                        matched = True
                        break
                if not matched:
                    continue
            found_provinces.append(p)
            if r not in found_regions:
                found_regions.append(r)
            if r in ["ภาคเหนือ", "ภาคตะวันออกเฉียงเหนือ", "ภาคกลาง", "กรุงเทพมหานครและปริมณฑล", "ภาคตะวันออก"]:
                if "ประเทศไทยตอนบน" not in found_regions:
                    found_regions.append("ประเทศไทยตอนบน")

    return found_regions, found_provinces


def _split_clauses_with_regions(text: str) -> list[tuple[list[str], list[str], str]]:
    """Split text into clauses and associate each with its active regions and provinces."""
    raw_clauses = _split_clauses(text)
    results = []
    current_regions: list[str] = []
    current_provinces: list[str] = []
    for c in raw_clauses:
        regs, provs = _find_region_match(c)
        if regs or provs:
            current_regions = regs
            current_provinces = provs
        results.append((list(current_regions), list(current_provinces), c))
    return results


def _allowed_days_for_region(reg_name: str, by_region: list[dict]) -> set[str]:
    """Compute allowed calendar day numbers for a region handling cross-month spans."""
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
            if area in PROVINCES_BY_REGION and reg_name in PROVINCES_BY_REGION[area]:
                covers = True
                break
        if covers:
            from_str = item.get("from")
            to_str = item.get("to")
            if from_str and to_str:
                try:
                    d_cur = date.fromisoformat(from_str)
                    d_end = date.fromisoformat(to_str)
                    while d_cur <= d_end:
                        allowed.add(str(d_cur.day))
                        d_cur += timedelta(days=1)
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
            if area in PROVINCES_BY_REGION and reg_name in PROVINCES_BY_REGION[area]:
                covers = True
                break
        if covers:
            for h in item.get("hazards", []):
                allowed.add(h)
    return allowed


def _region_original_has_word(reg_name: str, word: str, original_regional_clauses: list[tuple[list[str], list[str], str]]) -> bool:
    """Check if the original announcement text clause for a region contains the specific word (whitespace-insensitive)."""
    norm_word = _normalize_ws(word)
    for regs, _, clause in original_regional_clauses:
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
    rain_days: list[dict] | None = None,
    provinces_by_hazard: dict[str, list[str]] | None = None,
    periods_by_hazard: dict | None = None,
) -> list[str]:
    """Strict zero-trust validator for AI TMD warning summary against ground-truth facts."""
    problems: list[str] = []
    strong_words = strong_words or load_strong_words()

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

    current_regions: list[str] = []
    current_provinces: list[str] = []

    for s_clause in summary_clauses:
        matched_regions, matched_provinces = _find_region_match(s_clause)

        # Extract days mentioned in this clause
        clause_days = set(re.findall(r"\b\d{1,2}\b", s_clause))
        day_candidates = {d for d in clause_days if 1 <= int(d) <= 31 and d not in ["2569", "2568"]}
        if "2 เมตร" in s_clause or "2เมตร" in s_clause:
            day_candidates.discard("2")

        # Expand date ranges: 7-9, 7–9, 7—9, 7 ถึง 9, 7 - 9, 7 – 9
        for match in re.finditer(r"\b(\d{1,2})\s*(?:[-–—]|ถึง)\s*(\d{1,2})\b", s_clause):
            start_n, end_n = int(match.group(1)), int(match.group(2))
            if 1 <= start_n <= 31 and 1 <= end_n <= 31:
                if start_n <= end_n and (end_n - start_n) <= 15:
                    for d in range(start_n, end_n + 1):
                        day_candidates.add(str(d))
                elif start_n > end_n:
                    # Potential cross-month range, e.g. 30 ถึง 2
                    for item in by_region:
                        f_str, t_str = item.get("from"), item.get("to")
                        if f_str and t_str:
                            try:
                                d_c, d_e = date.fromisoformat(f_str), date.fromisoformat(t_str)
                                if d_c.day == start_n and d_e.day == end_n:
                                    while d_c <= d_e:
                                        day_candidates.add(str(d_c.day))
                                        d_c += timedelta(days=1)
                            except (ValueError, IndexError):
                                pass

        if matched_regions or matched_provinces:
            current_regions = list(matched_regions)
            current_provinces = list(matched_provinces)
        else:
            # Check if this clause contains hazard statements (Review Case 3)
            clause_has_hazard = any(any(kw in s_clause for kw in kws) for kws in HAZARD_KEYWORDS.values())
            if clause_has_hazard:
                if current_regions:
                    # Inherit region from previous clause (same behavior as original text)
                    matched_regions = list(current_regions)
                    matched_provinces = list(current_provinces)
                else:
                    problems.append(f"Hazard statement without region: '{s_clause}' (ภัย)")
                    continue

        # Item 4 & orphan statement handling without regions
        if not matched_regions:
            if day_candidates:
                for cand_day in day_candidates:
                    allowed_h_for_day = set()
                    for item in by_region:
                        f_str, t_str = item.get("from"), item.get("to")
                        if f_str and t_str:
                            try:
                                d_cur, d_end = date.fromisoformat(f_str), date.fromisoformat(t_str)
                                while d_cur <= d_end:
                                    if str(d_cur.day) == cand_day:
                                        allowed_h_for_day.update(item.get("hazards", []))
                                    d_cur += timedelta(days=1)
                            except (ValueError, IndexError):
                                pass
                    for h_type, kws in HAZARD_KEYWORDS.items():
                        if any(kw in s_clause for kw in kws):
                            if h_type not in allowed_h_for_day:
                                problems.append(
                                    f"Hazard '{h_type}' not allowed for date '{cand_day}' in by_region"
                                )
            continue

        # Check province against rain_days table if present
        if rain_days and matched_provinces:
            if day_candidates:
                for prov in matched_provinces:
                    # Collect all days this province appears in the rain_days table
                    # for the candidate day numbers mentioned in the clause.
                    # A province is acceptable when it appears in the table for
                    # *at least one* of the mentioned days (not necessarily every day),
                    # because a bulletin may cover a date-range for the whole region
                    # while the table lists individual provinces per day.
                    prov_found_any_day = False
                    for cand_day in day_candidates:
                        matching_rows = [
                            r for r in rain_days
                            if str(int(str(r["day"]).split("-")[-1])) == cand_day
                        ]
                        if matching_rows:
                            listed_provs = {p for r in matching_rows for p in r.get("provinces", [])}
                            if prov in listed_provs:
                                prov_found_any_day = True
                                break
                    if not prov_found_any_day:
                        # Province not in the rain_days table for ANY of the mentioned days
                        all_matching_rows = [
                            r for r in rain_days
                            if any(
                                str(int(str(r["day"]).split("-")[-1])) == d
                                for d in day_candidates
                            )
                        ]
                        if all_matching_rows:  # table has data for those days → flag
                            problems.append(
                                f"Province '{prov}' not in rain_days table for any of days {sorted(day_candidates)}"
                            )

            # Review Case 5: Check very_heavy in rain_days table
            # "ฝนตกหนักถึงหนักมาก" across a span of days is valid if the province
            # is marked with very_heavy: true for AT LEAST ONE day in that span.
            # It should only be rejected if the province has no very_heavy: true
            # day at all among the candidate days.
            if "หนักมาก" in s_clause:
                for prov in matched_provinces:
                    check_days = day_candidates
                    if not check_days:
                        matching_rows = [r for r in rain_days if prov in r.get("provinces", [])]
                    else:
                        matching_rows = [
                            r for r in rain_days
                            if str(int(str(r["day"]).split("-")[-1])) in check_days
                            and prov in r.get("provinces", [])
                        ]

                    has_vh = any(r.get("very_heavy") is True for r in matching_rows)
                    if not has_vh and matching_rows:
                        if check_days:
                            problems.append(
                                f"Word strength escalated for province '{prov}': 'หนักมาก' not in daily rain table (very_heavy is false) for days {sorted(check_days)} (ความแรง)"
                            )
                        else:
                            problems.append(
                                f"Word strength escalated for province '{prov}': 'หนักมาก' not in daily rain table (very_heavy is false) (ความแรง)"
                            )

        # Review Case 4: Check provinces against provinces_by_hazard
        if provinces_by_hazard and matched_provinces:
            for prov in matched_provinces:
                prov_code = PROVINCE_NAME_TO_CODE.get(prov)
                for h_type, kws in HAZARD_KEYWORDS.items():
                    if any(kw in s_clause for kw in kws):
                        allowed_p_list = provinces_by_hazard.get(h_type, [])
                        is_prov_allowed = prov in allowed_p_list or (prov_code and prov_code in allowed_p_list)
                        if not is_prov_allowed and h_type in provinces_by_hazard:
                            problems.append(
                                f"Hazard '{h_type}' not warned for province '{prov}' in provinces_by_hazard (ภัย)"
                            )

        for reg in matched_regions:
            # Check dates in this clause for reg (cross-month handled)
            allowed_days = _allowed_days_for_region(reg, by_region)
            if allowed_days:
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

            # Review Cases 1 & 2: Check dropping "บางแห่ง" from rain statements (regardless of rain phrasing)
            if "ฝน" in s_clause or "ตกหนัก" in s_clause or "หนักมาก" in s_clause:
                if "บางแห่ง" not in s_clause and "หลายพื้นที่" not in s_clause:
                    if _region_original_has_word(reg, "บางแห่ง", original_regional_clauses):
                        problems.append(
                            f"Word strength escalated for '{reg}': dropped 'บางแห่ง' from rain statement (ความแรง)"
                        )

            # Check word strength escalation
            for sw in strong_words:
                if sw in s_clause:
                    if not _region_original_has_word(reg, sw, original_regional_clauses):
                        problems.append(
                            f"Word strength escalated for '{reg}': '{sw}' not present in original announcement (ความแรง)"
                        )

    # Check completeness (all regions in by_region must be represented in summary)
    for item in by_region:
        areas = item.get("areas", [])
        covered = False
        for area in areas:
            if area in summary:
                covered = True
                break
            child_regs = REGIONS_COVERED.get(area, [])
            if any(cr in summary for cr in child_regs):
                covered = True
                break
            if area in PROVINCES_BY_REGION and any(p in summary for p in PROVINCES_BY_REGION[area]):
                covered = True
                break
            if area == "ประเทศไทยตอนบน":
                upper_regs = ["ภาคเหนือ", "ภาคตะวันออกเฉียงเหนือ", "ภาคกลาง", "กรุงเทพมหานครและปริมณฑล", "ภาคตะวันออก"]
                if any(p in summary for ur in upper_regs for p in PROVINCES_BY_REGION.get(ur, [])):
                    covered = True
                    break
        if not covered:
            problems.append(f"Missing required region from summary: {areas}")

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
        rain_days: list[dict] | None = None,
        provinces_by_hazard: dict[str, list[str]] | None = None,
        periods_by_hazard: dict | None = None,
    ) -> WarningAuditResult:
        """Cross-examine Qwen extraction with by_region without ever altering warning levels."""
        provider = os.getenv("LLM_PROVIDER", "").lower()
        if provider != "qwen" and self.requester is None:
            return WarningAuditResult(matched=True, discrepancies=[])

        rain_days_str = ""
        if rain_days:
            rain_days_str = f"\n\nตารางฝนรายวัน:\n{json.dumps(rain_days, ensure_ascii=False)}"

        prompt = f"ประกาศ: {title}\n\nเนื้อหา:\n{body}{rain_days_str}\n\nจงตอบเป็น JSON:"
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
2. ห้ามเปลี่ยนความแรงของถ้อยคำ ถ้ามี "บางแห่ง" ห้ามเขียนเป็น "หลายพื้นที่" และห้ามตัด "บางแห่ง" ออก
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
        self.last_error: str | None = None
        self.validation_failed: bool = False

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
        rain_days: list[dict] | None = None,
        provinces_by_hazard: dict[str, list[str]] | None = None,
        periods_by_hazard: dict | None = None,
    ) -> WarningSummaryResult | None:
        """Generate verified AI summary for warning card; returns None on validation failure or error."""
        self.last_error = None
        self.validation_failed = False

        provider = os.getenv("LLM_PROVIDER", "").lower()
        if provider != "qwen" and self.requester is None:
            return None

        prompt = f"ประกาศ: {title}\n\nเนื้อหาทางการ:\n{body}\n\nจงเรียบเรียงสรุป 2-3 บรรทัด:"
        try:
            raw_summary = self._call_qwen(prompt).strip()
            # Separate title from body with a newline so word-boundary checks in
            # _region_original_has_word() do not bleed title tokens into the first
            # body clause (e.g. "หนักมาก" in the title should not count as being
            # present in ประเทศไทยตอนบน's section).
            full_context = f"{title}\n{body}"
            problems = validate_warning_summary(
                summary=raw_summary,
                original_text=full_context,
                by_region=by_region,
                rain_days=rain_days,
                provinces_by_hazard=provinces_by_hazard,
                periods_by_hazard=periods_by_hazard,
            )
            if problems:
                log.warning("Warning summary failed validation: %s", problems)
                self.validation_failed = True
                return None

            return WarningSummaryResult(
                summary_text=raw_summary,
                attribution="สรุปโดย AI จากประกาศกรมอุตุนิยมวิทยา · ดูประกาศฉบับจริง",
                valid=True,
                error=None,
            )
        except Exception as exc:
            log.warning("WarningExplainer error during summarize: %s", exc)
            self.last_error = str(exc)
            return None
