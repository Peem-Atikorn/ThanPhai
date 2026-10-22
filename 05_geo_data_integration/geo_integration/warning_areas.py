"""Find the provinces that a Thai weather warning (TMD) names, by province or by TMD forecast region.

TMD warnings name areas in two ways: regions ("ภาคตะวันออกและภาคใต้", "กรุงเทพมหานครและปริมณฑล") and
province lists ("จังหวัดเชียงราย พะเยา น่าน และแพร่", only the first name has a prefix).
"""

import re

from geo_integration.gazetteer import ALIASES, Gazetteer

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


def warning_provinces(gazetteer: Gazetteer, text: str) -> list[str]:
    """Province codes that a warning text names, sorted. Empty when it names none ("บริเวณประเทศไทย")."""
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
    codes = {code for _, _, code in kept} | {keys[name] for name in names if name in keys}
    return sorted(codes)


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
