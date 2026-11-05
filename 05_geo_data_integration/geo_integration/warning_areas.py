"""Find the provinces that a Thai weather warning (TMD) names, by province or by TMD forecast region.

TMD warnings name areas in two ways: regions ("ภาคตะวันออกและภาคใต้", "กรุงเทพมหานครและปริมณฑล") and
province lists ("จังหวัดเชียงราย พะเยา น่าน และแพร่", only the first name has a prefix).
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from typing import TypeVar

from geo_integration.gazetteer import ALIASES, Gazetteer

H = TypeVar("H")
Days = tuple[date, date]

# TMD forecast regions, as its daily forecast groups provinces. These are meteorological regions, not the
# six geographic ones: กำแพงเพชร, พิจิตร and เพชรบูรณ์ are in the north, นครนายก in the east, and
# เพชรบุรี and ประจวบคีรีขันธ์ in the southern east coast (checked on tmd.go.th/forecast/daily and
# /weather/region/*, 2026-10-23). Sub-regions ("ภาคเหนือตอนล่าง", "ภาคใต้ตอนบน") have no fixed province list,
# so they count as the whole region: a warning may then reach a few provinces too many, never too few.
NORTH = ("เชียงราย", "แม่ฮ่องสอน", "เชียงใหม่", "ลำพูน", "ลำปาง", "พะเยา", "แพร่", "น่าน", "อุตรดิตถ์", "ตาก",
         "สุโขทัย", "พิษณุโลก", "พิจิตร", "กำแพงเพชร", "เพชรบูรณ์", "นครสวรรค์", "อุทัยธานี")
NORTHEAST = ("เลย", "หนองคาย", "บึงกาฬ", "หนองบัวลำภู", "อุดรธานี", "สกลนคร", "นครพนม", "มุกดาหาร", "ขอนแก่น",
             "กาฬสินธุ์", "มหาสารคาม", "ร้อยเอ็ด", "ชัยภูมิ", "นครราชสีมา", "บุรีรัมย์", "สุรินทร์", "ศรีสะเกษ",
             "ยโสธร", "อำนาจเจริญ", "อุบลราชธานี")
CENTRAL = ("ชัยนาท", "สิงห์บุรี", "อ่างทอง", "ลพบุรี", "สระบุรี", "พระนครศรีอยุธยา", "สุพรรณบุรี", "กาญจนบุรี",
           "ราชบุรี", "สมุทรสงคราม")
BANGKOK_AND_VICINITY = ("กรุงเทพมหานคร", "นนทบุรี", "ปทุมธานี", "สมุทรปราการ", "สมุทรสาคร", "นครปฐม")
EAST = ("นครนายก", "ปราจีนบุรี", "สระแก้ว", "ฉะเชิงเทรา", "ชลบุรี", "ระยอง", "จันทบุรี", "ตราด")
SOUTH_EAST_COAST = ("เพชรบุรี", "ประจวบคีรีขันธ์", "ชุมพร", "สุราษฎร์ธานี", "นครศรีธรรมราช", "พัทลุง", "สงขลา",
                    "ปัตตานี", "ยะลา", "นราธิวาส")
SOUTH_WEST_COAST = ("ระนอง", "พังงา", "ภูเก็ต", "กระบี่", "ตรัง", "สตูล")

REGIONS = {
    "ภาคเหนือ": NORTH,
    "ภาคตะวันออกเฉียงเหนือ": NORTHEAST,
    "ภาคอีสาน": NORTHEAST,
    "ภาคกลาง": CENTRAL,
    "กรุงเทพมหานครและปริมณฑล": BANGKOK_AND_VICINITY,
    "กรุงเทพฯและปริมณฑล": BANGKOK_AND_VICINITY,
    "กรุงเทพและปริมณฑล": BANGKOK_AND_VICINITY,
    "กทม.และปริมณฑล": BANGKOK_AND_VICINITY,
    "ปริมณฑล": BANGKOK_AND_VICINITY[1:],
    "ภาคตะวันออก": EAST,
    "ภาคใต้ฝั่งตะวันออก": SOUTH_EAST_COAST,
    "ภาคใต้ฝั่งตะวันตก": SOUTH_WEST_COAST,
    "ภาคใต้": SOUTH_EAST_COAST + SOUTH_WEST_COAST,
    # Upper Thailand: everything but the south (live issue 3, 2026-11-02: "ส่วนภาคเหนือ ภาคกลาง … และภาคตะวันออก").
    "ประเทศไทยตอนบน": NORTH + NORTHEAST + CENTRAL + BANGKOK_AND_VICINITY + EAST,
}
# Longest first, so "ภาคตะวันออกเฉียงเหนือ" is not read as "ภาคตะวันออก" and "ภาคใต้ฝั่ง…" not as "ภาคใต้".
REGION_PATTERN = re.compile("|".join(re.escape(name) for name in sorted(REGIONS, key=len, reverse=True)))
# TMD writes the coasts several ways: "ภาคใต้ฝั่งตะวันออก", "ภาคใต้(ฝั่งตะวันออก)", "ภาคใต้ ฝั่งตะวันออก".
COAST_SPELLING = re.compile(r"ภาคใต้\s*\(?\s*ฝั่ง\s*(ตะวันออก|ตะวันตก)\s*\)?")
# Short province names are ordinary words too ("เลย" = at all, "ตาก" = to dry, "แพร่" in "เผยแพร่").
# Official names this short count only as a whole item of a list: after a space, a prefix or "และ", and
# before a space, punctuation or "และ". Abbreviations ("กทม") are not words, so they match anywhere.
SHORT_NAME = 4
BEFORE = re.compile(r"(?:^|[\s,:;(]|จังหวัด|จ\.|และ)$")
AFTER = re.compile(r"^(?:$|[\s,:;).]|และ)")
# Words that start a new statement about other areas ("ส่วนภาคเหนือตอนล่าง … ยังคงมีฝนฟ้าคะนอง"). "ส่วนมาก" and
# "ส่วนใหญ่" mean "mostly" and do not.
# A title joins two subjects with "และ" + weather + its own area: "อากาศแปรปรวนบริเวณประเทศไทยตอนบน
# และฝนตกหนักถึงหนักมากบริเวณภาคใต้". Without "บริเวณ" in the same word, "และมีฝนตกหนักบางแห่ง" stays with its area.
PART_START = re.compile(r"(?=ส่วน(?!มาก|ใหญ่)|หลังจากนั้น|สำหรับ|ขอให้|อนึ่ง|\sและ(?:มี)?(?:ฝน|พายุ|คลื่น|ลม|น้ำ|อากาศ)\S*บริเวณ)")
# "ส่วนภาคเหนือ … จะได้รับผลกระทบในระยะถัดไป": the same weather as the statement before, later.
SAME_IMPACT = re.compile(r"ผลกระทบ|เช่นเดียวกัน")


def warning_parts(*texts: str) -> list[str]:
    """Warning texts (title, headline) cut into statements, each with its own areas and its own hazard words."""
    return [part.strip() for text in texts for part in PART_START.split(text) if part.strip()]


def provinces_by_hazard(gazetteer: Gazetteer, texts: str | Iterable[str], classify: Callable[[str], Iterable[H]]) -> dict[H, list[str]]:
    """Hazard -> province codes, sorted: a province counts for a hazard only where one statement names both."""
    return {hazard: sorted(areas) for hazard, areas in hazard_areas(gazetteer, texts, classify).items()}


@dataclass
class Statement:
    """One statement of a warning: its hazards, the provinces it reaches and how, its days and its wording."""

    hazards: list
    provinces: set[str]
    # Provinces named by name ("จังหวัดสงขลา"); the others are reached only through a region.
    named: set[str]
    # Regions as written ("ประเทศไทยตอนบน", "ภาคใต้"), for showing the warning by region.
    regions: list[str]
    days: Days | None
    # The words that say how bad it is: this statement's, and the one's it takes hazards, days or areas from.
    wording: str


def warning_statements(gazetteer: Gazetteer, texts: str | Iterable[str], classify: Callable[[str], Iterable[H]],
                       read_days: Callable[[str], Days | None] = lambda _: None) -> list[Statement]:
    """The statements of warning texts that name a hazard and an area, in order.

    Issue 12 has heavy rain in the east and south, "ส่วน" the lower north only thunderstorms: the north must not
    get the heavy-rain (flood) warning. The title and the headline are cut separately, so the title's heavy rain
    in the south does not run into the headline's first areas. A statement with no hazard word of its own sets
    nothing, unless it says it gets the same impact ("ได้รับผลกระทบ"): then it takes the hazards, and if it gives
    none of its own the days, of the statement before it in the same text.

    Advice that names hazards but no area belongs to the statement before it: the PDF of issue 288/2569 has
    "… บริเวณภาคใต้ … ขอให้ประชาชนระวังอันตรายจากฝนที่ตกสะสม ซึ่งอาจทำให้เกิดน้ำท่วมฉับพลัน" (flood in the south).
    """
    found: list[Statement] = []
    for text in [texts] if isinstance(texts, str) else texts:
        previous: Statement | None = None
        for part in warning_parts(text):
            own = list(classify(part))
            named, regional = _read_provinces(gazetteer, part)
            same = not own and bool(SAME_IMPACT.search(part)) and previous is not None
            advice = bool(own) and not (named or regional) and previous is not None
            hazards = own or (previous.hazards if same else [])
            days = read_days(part) or (previous.days if same or advice else None)
            if advice:
                named, regional, regions = previous.named, previous.provinces - previous.named, previous.regions
            else:
                regions = list(dict.fromkeys(m.group() for m in REGION_PATTERN.finditer(COAST_SPELLING.sub(r"ภาคใต้ฝั่ง\1", part))))
            wording = f"{previous.wording} {part}" if (same or advice) else part
            statement = Statement(hazards, named | regional, named, regions, days, wording)
            if hazards and statement.provinces:
                found.append(statement)
            previous = statement
    return found


def hazard_areas(gazetteer: Gazetteer, texts: str | Iterable[str], classify: Callable[[str], Iterable[H]],
                 read_days: Callable[[str], Days | None] = lambda _: None) -> dict[H, dict[str, Days | None]]:
    """Hazard -> province code -> (first, last day) the statements naming both give, or None: the warning's own days.

    Issue 3 (2026-11-02): the title says 5-9 Nov for the south, the headline 5-7 Nov for upper Thailand. A province
    named in several statements gets the span of all of them; one without days there keeps the warning's days.
    """
    found: dict[H, dict[str, Days | None]] = {}
    for statement in warning_statements(gazetteer, texts, classify, read_days):
        for hazard in statement.hazards:
            areas = found.setdefault(hazard, {})
            for code in statement.provinces:
                areas[code] = _span(areas[code], statement.days) if code in areas else statement.days
    return found


def _span(a: Days | None, b: Days | None) -> Days | None:
    """Both statements' days together; None (the warning's whole window) wins."""
    return None if a is None or b is None else (min(a[0], b[0]), max(a[1], b[1]))


def warning_provinces(gazetteer: Gazetteer, text: str) -> list[str]:
    """Province codes that a warning text names, sorted. Empty when it names none ("บริเวณประเทศไทย")."""
    named, regional = _read_provinces(gazetteer, text)
    return sorted(named | regional)


def table_provinces(gazetteer: Gazetteer, row: str) -> list[str]:
    """Province codes of a bulletin's daily-table row, sorted. Its "ภาคเหนือ:" labels head the region's own list and
    do not mean the whole region; "รวมทั้งกรุงเทพมหานครและปริมณฑล" still does."""
    return warning_provinces(gazetteer, re.sub(r"ภาค[ก-๙]*\s*:", " ", row))


def named_provinces(gazetteer: Gazetteer, text: str) -> list[str]:
    """Province codes that a warning text names by province ("จังหวัดสงขลา", "กทม"), sorted; regions do not count."""
    return sorted(_read_provinces(gazetteer, text)[0])


def _read_provinces(gazetteer: Gazetteer, text: str) -> tuple[set[str], set[str]]:
    """(codes named by province, codes reached through a region)."""
    text = COAST_SPELLING.sub(r"ภาคใต้ฝั่ง\1", text)
    names: set[str] = set()
    # Blank out each region once it is read, so the province scan below cannot also match inside it.
    for match in REGION_PATTERN.finditer(text):
        names.update(REGIONS[match.group()])
    text = REGION_PATTERN.sub(lambda m: " " * len(m.group()), text)

    keys = _province_keys(gazetteer)
    hits: list[tuple[int, int, str]] = []
    for key, code in keys.items():
        for match in re.finditer(re.escape(key), text):
            start, end = match.span()
            if len(key) <= SHORT_NAME and key not in ALIASES and not (BEFORE.search(text[:start]) and AFTER.match(text[end:])):
                continue
            hits.append((start, end, code))
    # Longest match wins where names overlap ("กรุงเทพมหานคร" over the alias "กรุงเทพ").
    hits.sort(key=lambda h: (-(h[1] - h[0]), h[0]))
    kept: list[tuple[int, int, str]] = []
    for hit in hits:
        if all(hit[1] <= k[0] or hit[0] >= k[1] for k in kept):
            kept.append(hit)
    return {code for _, _, code in kept}, {keys[name] for name in names if name in keys}


def _province_keys(gazetteer: Gazetteer) -> dict[str, str]:
    """Thai province names and their common short forms ("กทม", "โคราช") -> province code."""
    keys = {}
    for area in gazetteer.areas:
        if area.level == "province":
            keys[area.name_th] = area.code
    for alias, official in ALIASES.items():
        if official in keys and not alias.isascii():
            keys[alias] = keys[official]
    return keys
