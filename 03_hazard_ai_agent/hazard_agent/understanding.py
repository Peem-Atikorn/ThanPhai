"""Deterministic question understanding: intent, hazards, area, and time window. No LLM needed."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from geo_integration import Area, Gazetteer
from thanphai_shared import HazardType

BANGKOK = timezone(timedelta(hours=7), "Asia/Bangkok")

HAZARD_WORDS = {
    HazardType.FLOOD: ("น้ำท่วม", "ท่วม", "น้ำล้น", "ล้นตลิ่ง", "น้ำป่า", "flood"),
    HazardType.RAIN_STORM: ("ฝนตก", "ฝนหนัก", "ฝน", "พายุ", "ลมแรง", "rain", "storm"),
    HazardType.DROUGHT: ("ภัยแล้ง", "แล้ง", "ขาดน้ำ", "ฝนทิ้งช่วง", "drought"),
    HazardType.FIRE: ("ไฟป่า", "ไฟไหม้", "เผา", "fire"),
    # Smoke and haze are what people breathe, so they are air-quality questions.
    HazardType.AIR_QUALITY: ("ฝุ่น", "pm2.5", "pm 2.5", "pm25", "หมอกควัน", "คุณภาพอากาศ", "มลพิษทางอากาศ", "aqi",
                             "air quality", "smoke", "haze"),
    HazardType.HEAT: ("ร้อนจัด", "อากาศร้อน", "ร้อนมาก", "ดัชนีความร้อน", "ฮีตสโตรก", "ฮีทสโตรก", "ลมแดด", "heat stroke",
                      "heatstroke", "heat index", "heat wave", "heatwave"),
    HazardType.EARTHQUAKE: ("แผ่นดินไหว", "อาฟเตอร์ช็อก", "aftershock", "earthquake", "quake", "seismic"),
}
DAM_WORDS = ("เขื่อน", "อ่างเก็บน้ำ", "ปริมาณน้ำในอ่าง", "dam", "reservoir")
# Questions about river flow are always river questions; river and bank words only when no area is named
# ("อำเภอเสนาน้ำล้นตลิ่งไหม" is better answered by the district's flood assessment).
FLOW_WORDS = ("อัตราการไหล", "ปริมาณน้ำไหล", "น้ำไหลผ่าน", "ไหลเท่า", "ลบ.ม./วินาที", "ลบ.ม./วิ", "discharge", "flow rate")
RIVER_WORDS = ("แม่น้ำ", "เจ้าพระยา", "ล้นตลิ่ง", "river")
STATION_CODE = re.compile(r"(?<![A-Za-z])[A-Z]{1,3}\.\d{1,3}[A-Z]?(?![A-Za-z0-9])")
EXPLAIN_WORDS = ("ทำไม", "เพราะอะไร", "สาเหตุ", "why")
PREPARE_WORDS = ("เตรียม", "ต้องทำ", "ทำยังไง", "ทำอย่างไร", "ควรทำ", "อพยพ", "prepare", "what should")
EMERGENCY_WORDS = ("ช่วยด้วย", "ติดอยู่", "บาดเจ็บ", "จมน้ำ", "ฉุกเฉิน", "help", "trapped", "injured")
# Relative Thai/English time words -> (start offset days, end offset days).
TIME_WORDS = {
    "วันนี้": (0, 1), "today": (0, 1), "คืนนี้": (0, 1), "tonight": (0, 1),
    "พรุ่งนี้": (1, 2), "tomorrow": (1, 2), "มะรืน": (2, 3),
    "สัปดาห์นี้": (0, 7), "อาทิตย์นี้": (0, 7), "this week": (0, 7),
    "หน้าแล้ง": (0, 90), "เดือนหน้า": (30, 60),
}


@dataclass
class Understanding:
    intent: str  # risk | explain | prepare | emergency | dam | river | out_of_scope
    hazards: list[HazardType]
    areas: list[Area]
    window_start: datetime
    window_end: datetime
    language: str
    notes: list[str] = field(default_factory=list)
    # True when the user asks about the future; the system only has current observations.
    future: bool = False

    @property
    def area(self) -> Area | None:
        return self.areas[0] if self.areas else None


def detect_language(text: str) -> str:
    return "th" if re.search(r"[฀-๿]", text) else "en"


def detect_hazards(text: str) -> list[HazardType]:
    lowered = text.lower()
    found = [h for h, words in HAZARD_WORDS.items() if any(w in lowered for w in words)]
    # "ฝน" alone is rain; "ฝนทิ้งช่วง" is drought, not rain.
    if HazardType.DROUGHT in found and "ฝนทิ้งช่วง" in lowered and not any(w in lowered for w in ("ฝนตก", "ฝนหนัก", "พายุ")):
        found.remove(HazardType.RAIN_STORM)
    return found


# Words that turn a question into one about the future ("น้ำจะท่วมไหม", "will it flood").
FUTURE_WORDS = ("จะ", "will ", "going to", "forecast", "พยากรณ์", "คาดการณ์", "แนวโน้ม")
# Time words that mean "right now" and override a future marker.
NOW_WORDS = ("ตอนนี้", "ขณะนี้", "now", "currently", "right now")


def detect_window(text: str, now: datetime) -> tuple[datetime, datetime, bool]:
    """Return (start, end, explicit). explicit is False when no time words were found."""
    lowered = text.lower()
    match = re.search(r"(\d+)\s*(วัน|days?)", lowered)
    if match:
        return now, now + timedelta(days=int(match.group(1))), True
    for word, (start, end) in TIME_WORDS.items():
        if word in lowered:
            day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            return max(now, day + timedelta(days=start)), day + timedelta(days=end), True
    return now, now + timedelta(days=3), False


def asks_about_future(text: str, start: datetime, end: datetime, explicit: bool, now: datetime) -> bool:
    lowered = text.lower()
    if any(w in lowered for w in NOW_WORDS):
        return False
    if explicit and (start.date() > now.date() or end - start > timedelta(days=1)):
        return True
    return any(w in lowered for w in FUTURE_WORDS)


def understand(text: str, gazetteer: Gazetteer, now: datetime) -> Understanding:
    lowered = text.lower()
    language = detect_language(text)
    start, end, explicit = detect_window(text, now)
    future = asks_about_future(text, start, end, explicit, now)
    areas = gazetteer.find_in_text(text)
    if any(w in lowered for w in EMERGENCY_WORDS):
        return Understanding("emergency", detect_hazards(text), areas, start, end, language)
    if any(w in lowered for w in DAM_WORDS):
        # A real tambon is named "เขื่อน" (TH440310, Maha Sarakham); in a dam question the word is not a place.
        places = [a for a in areas if a.name_th not in DAM_WORDS]
        return Understanding("dam", [], places, start, end, language)
    if any(w in lowered for w in RIVER_WORDS):
        # Tambons named like river words exist (ต.แม่น้ำ, Ko Samui): "แม่น้ำเจ้าพระยา" matched it live.
        # Such a name counts as a place only when written as one ("ต.แม่น้ำ", "ตำบลแม่น้ำ").
        areas = [a for a in areas if a.name_th not in RIVER_WORDS
                 or f"ต.{a.name_th}" in text or f"ตำบล{a.name_th}" in text]
    if (any(w in lowered for w in FLOW_WORDS) or STATION_CODE.search(text)
            or (any(w in lowered for w in RIVER_WORDS) and not areas)):
        return Understanding("river", [], areas, start, end, language)
    hazards = detect_hazards(text)
    if any(w in lowered for w in PREPARE_WORDS):
        intent = "prepare"
    elif any(w in lowered for w in EXPLAIN_WORDS):
        intent = "explain"
    elif hazards or areas:
        intent = "risk"
    else:
        intent = "out_of_scope"
    notes = []
    if intent != "out_of_scope" and not hazards:
        hazards = list(HazardType)
        notes.append("no hazard named; checking all four")
    if intent in ("risk", "explain") and not areas:
        notes.append("no area found")
    if areas and sum(1 for a in areas if a.level == areas[0].level) > 1:
        notes.append("ambiguous area")
    return Understanding(intent, hazards, areas, start, end, language, notes, future)
