"""Deterministic question understanding: intent, hazards, area, and time window. No LLM needed."""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from geo_integration import Area, Gazetteer
from geo_integration.gazetteer import MUEANG_QUERY
from geo_integration.regions import find_regions, mask_regions
from thanphai_shared import HazardType

BANGKOK = timezone(timedelta(hours=7), "Asia/Bangkok")

HAZARD_WORDS = {
    HazardType.FLOOD: ("น้ำท่วม", "ท่วม", "น้ำล้น", "ล้นตลิ่ง", "น้ำป่า", "flood"),
    HazardType.RAIN_STORM: ("ฝนตก", "ฝนหนัก", "ฝน", "พายุ", "ลมแรง", "rain", "storm"),
    HazardType.DROUGHT: ("ภัยแล้ง", "แล้ง", "ขาดน้ำ", "ฝนทิ้งช่วง", "drought"),
    # Satellite hotspots (FIRMS) are the fire data, so "มีจุดความร้อนไหม" is a fire question.
    HazardType.FIRE: ("ไฟป่า", "ไฟไหม้", "เผา", "จุดความร้อน", "hotspot", "hot spot", "fire"),
    # Smoke and haze are what people breathe, so they are air-quality questions.
    HazardType.AIR_QUALITY: ("ฝุ่น", "pm2.5", "pm 2.5", "pm25", "หมอกควัน", "คุณภาพอากาศ", "มลพิษทางอากาศ", "aqi",
                             "air quality", "smoke", "haze"),
    # Temperature questions ("อุณหภูมิตอนเที่ยงจะเท่าไหร่", "กี่องศา") are answered with the heat forecast. No bare
    # "ร้อน"/"hot": they are inside "จุดความร้อน"/"hotspot", which are fire questions.
    HazardType.HEAT: ("ร้อนจัด", "อากาศร้อน", "ร้อนมาก", "ร้อนไหม", "ร้อนเหมือน", "ดัชนีความร้อน", "อุณหภูมิ", "กี่องศา",
                      "ฮีตสโตรก", "ฮีทสโตรก", "ลมแดด", "heat stroke", "heatstroke", "heat index", "heat wave", "heatwave",
                      "temperature", "how hot", "feels like"),
    HazardType.EARTHQUAKE: ("แผ่นดินไหว", "อาฟเตอร์ช็อก", "aftershock", "earthquake", "quake", "seismic"),
    HazardType.MARINE: ("คลื่นลมแรง", "คลื่นสูง", "คลื่นทะเล", "ออกเรือ", "เดินเรือ", "ลงเรือ", "ทะเล", "high waves",
                             "rough sea"),
    HazardType.TROPICAL_CYCLONE: ("ไต้ฝุ่น", "พายุโซนร้อน", "ดีเปรสชัน", "พายุหมุน", "ไซโคลน", "typhoon", "cyclone",
                                  "tropical storm"),
    HazardType.LANDSLIDE: ("ดินถล่ม", "ดินสไลด์", "โคลนถล่ม", "ภูเขาถล่ม", "landslide", "mudslide"),
}
# Where to go: answered from DDPM's shelter list. Before the off-topic words ("ที่พักพิง" contains "ที่พัก") and
# before prepare ("อพยพไปที่ไหน" asks for a place, "ต้องอพยพอย่างไร" for guidance).
SHELTER_WORDS = ("ศูนย์พักพิง", "ศูนย์อพยพ", "จุดอพยพ", "ที่พักพิง", "ศูนย์หลบภัย", "อพยพไปที่ไหน", "อพยพไปไหน",
                 "หนีน้ำไปที่ไหน", "หนีน้ำไปไหน", "shelter", "evacuation centre", "evacuation center")
DAM_WORDS = ("เขื่อน", "อ่างเก็บน้ำ", "ปริมาณน้ำในอ่าง", "dam", "reservoir")
DAM_HISTORY_WORDS = ("เทียบ", "เปรียบเทียบ", "เมื่อวาน", "สัปดาห์ก่อน", "อาทิตย์ก่อน", "history", "เทียบกับ")
# A river is named "แม่น้ำน่าน" / "ลำน้ำน่าน"; the name after these is the river's, not a place's.
RIVER_PREFIXES = ("แม่น้ำ", "ลำน้ำ")
# Questions about river flow are always river questions; river and bank words only when no area is named
# ("อำเภอเสนาน้ำล้นตลิ่งไหม" is better answered by the district's flood assessment).
FLOW_WORDS = ("อัตราการไหล", "ปริมาณน้ำไหล", "น้ำไหลผ่าน", "ไหลเท่า", "ลบ.ม./วินาที", "ลบ.ม./วิ", "discharge", "flow rate")
RIVER_WORDS = ("แม่น้ำ", "เจ้าพระยา", "ล้นตลิ่ง", "river")
STATION_CODE = re.compile(r"(?<![A-Za-z])[A-Z]{1,3}\.\d{1,3}[A-Z]?(?![A-Za-z0-9])")
EXPLAIN_WORDS = ("ทำไม", "เพราะอะไร", "สาเหตุ", "why")
# Everyday plans that depend on rain where the user is; answered from that area's rain assessment and forecast.
DAILY_RAIN_WORDS = ("ตากผ้า", "ซักผ้า", "พกร่ม", "เอาร่ม", "ออกไปข้างนอก", "ไปเที่ยว", "ปั่นจักรยาน", "วิ่งออกกำลัง",
                    "ล้างรถ", "umbrella", "laundry")
PREPARE_WORDS = ("เตรียม", "ต้องทำ", "ทำยังไง", "ทำอย่างไร", "ควรทำ", "อพยพ", "prepare", "what should")
EMERGENCY_WORDS = ("ช่วยด้วย", "ติดอยู่", "บาดเจ็บ", "จมน้ำ", "ฉุกเฉิน", "help", "trapped", "injured")
# Questions about a place that are not about hazards ("ชลบุรี ของกินอร่อยไหม"): without these the place name
# alone made them risk questions and the reply was a hazard report.
OFF_TOPIC_WORDS = ("อร่อย", "ของกิน", "ร้านอาหาร", "ร้านกาแฟ", "คาเฟ่", "กินอะไร", "ของฝาก", "ที่เที่ยว", "เที่ยวไหน",
                   "เที่ยวที่ไหน", "ที่พัก", "โรงแรม", "รีสอร์ท", "ช้อปปิ้ง", "ราคา", "restaurant", "food", "hotel", "cafe")
SOURCE_WORDS = ("แหล่งข้อมูล", "ข้อมูลจากไหน", "ข้อมูลมาจากไหน", "ที่มาของข้อมูล", "ใช้ข้อมูลจาก", "data source")
GREETING = re.compile(r"(?:(?:สวัสดี|หวัดดี)(?:\s*(?:ครับ|ค่ะ|คะ))?|hello|hi|hey)[!?.\s]*")
# A past period or a count turns an earthquake question about a place into the list of events there.
QUAKE_LIST_WORDS = ("เดือนนี้", "เดือนที่แล้ว", "เดือนก่อน", "สัปดาห์", "อาทิตย์", "ย้อนหลัง", "ที่ผ่านมา", "กี่ครั้ง", "กี่รอบ",
                    "บ่อย", "ที่ไหนบ้าง", "รายการ", "วันก่อน", "เมื่อวาน", "this month", "last month", "this week", "last week",
                    "yesterday", "past", "how many", "list")
# Relative Thai/English time words -> (start offset days, end offset days).
TIME_WORDS = {
    "วันนี้": (0, 1), "today": (0, 1), "คืนนี้": (0, 1), "tonight": (0, 1),
    "พรุ่งนี้": (1, 2), "tomorrow": (1, 2), "มะรืน": (2, 3),
    "สัปดาห์นี้": (0, 7), "อาทิตย์นี้": (0, 7), "this week": (0, 7),
    "หน้าแล้ง": (0, 90), "เดือนหน้า": (30, 60),
}


@dataclass
class Understanding:
    intent: str  # risk | explain | prepare | emergency | dam | river | shelter | greeting | sources | out_of_scope
    hazards: list[HazardType]
    areas: list[Area]
    window_start: datetime
    window_end: datetime
    language: str
    notes: list[str] = field(default_factory=list)
    # True when the user asks about the future; the system only has current observations.
    future: bool = False
    regions: list[str] = field(default_factory=list)

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
    if HazardType.MARINE in found and "คลื่นลมแรง" in lowered and not any(w in lowered for w in ("ฝนตก", "ฝนหนัก", "พายุ", "ฝน")):
        found.remove(HazardType.RAIN_STORM)
    # "ไต้ฝุ่น" (typhoon) contains "ฝุ่น" (dust): count dust only if it appears outside the word typhoon.
    if HazardType.AIR_QUALITY in found and not any(w in lowered.replace("ไต้ฝุ่น", "") for w in HAZARD_WORDS[HazardType.AIR_QUALITY]):
        found.remove(HazardType.AIR_QUALITY)
    # "จุดความร้อน" (fire hotspot) contains "ร้อนไหม" in "มีจุดความร้อนไหม": count heat only outside that word.
    if HazardType.HEAT in found and not any(w in lowered.replace("จุดความร้อน", "") for w in HAZARD_WORDS[HazardType.HEAT]):
        found.remove(HazardType.HEAT)
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


def only_in_river_name(name: str, text: str) -> bool:
    """"แม่น้ำน่าน" names the Nan river, not Nan province: true when every mention of the place name is part of a
    river name and none is written as a place ("จ.น่าน", "จังหวัดน่าน")."""
    if not name or any(f"{p}{name}" in text for p in ("จ.", "จังหวัด", "อ.", "อำเภอ", "ต.", "ตำบล")):
        return False
    total = text.count(name)
    return total > 0 and total == sum(text.count(p + name) for p in RIVER_PREFIXES)


def understand(text: str, gazetteer: Gazetteer, now: datetime) -> Understanding:
    lowered = text.lower()
    language = detect_language(text)
    start, end, explicit = detect_window(text, now)
    future = asks_about_future(text, start, end, explicit, now)
    areas = [a for a in gazetteer.find_in_text(text) if not only_in_river_name(a.name_th, text)]
    regions = find_regions(text)
    if regions and gazetteer.find_in_text(mask_regions(text)):
        regions = []  # A named province or district is more specific than its region.
    if any(w in lowered for w in EMERGENCY_WORDS):
        return Understanding("emergency", detect_hazards(text), areas, start, end, language)
    if GREETING.fullmatch(lowered.strip()):
        return Understanding("greeting", [], [], start, end, language)
    if any(w in lowered for w in SHELTER_WORDS):
        return Understanding("shelter", detect_hazards(text), areas, start, end, language, regions=regions)
    if any(w in lowered for w in DAM_WORDS):
        # A real tambon is named "เขื่อน" (TH440310, Maha Sarakham); in a dam question the word is not a place.
        places = [a for a in areas if a.name_th not in DAM_WORDS]
        if any(w in lowered for w in DAM_HISTORY_WORDS):
            return Understanding("dam_history", [], places, start, end, language, regions=regions)
        return Understanding("dam", [], places, start, end, language, regions=regions)
    if any(w in lowered for w in RIVER_WORDS):
        # Tambons named like river words exist (ต.แม่น้ำ, Ko Samui): "แม่น้ำเจ้าพระยา" matched it live.
        # Such a name counts as a place only when written as one ("ต.แม่น้ำ", "ตำบลแม่น้ำ").
        areas = [a for a in areas if a.name_th not in RIVER_WORDS
                 or f"ต.{a.name_th}" in text or f"ตำบล{a.name_th}" in text]
    # "แม่น้ำน่านที่พิษณุโลก" names a river and a place: a river question for that place, unless it also asks
    # about a hazard ("แม่น้ำเจ้าพระยาท่วมอยุธยาไหม" stays a flood question for Ayutthaya).
    # With a place, only a named river counts: "อำเภอเสนาน้ำล้นตลิ่งไหม" keeps the district's flood assessment.
    river_named = any(w in lowered for w in ("แม่น้ำ", "เจ้าพระยา", "river"))
    river_asked = (any(w in lowered for w in RIVER_WORDS) and not areas) or (river_named and (
        not detect_hazards(text) or any(w in text for w in ("ล้นตลิ่ง", "ระดับน้ำ"))))
    if any(w in lowered for w in FLOW_WORDS) or STATION_CODE.search(text) or river_asked:
        return Understanding("river", [], areas, start, end, language, regions=regions)
    # These requests need numeric source records, rather than a model-generated risk summary.
    # Keep ordinary area risk questions (e.g. "เชียงใหม่แผ่นดินไหวไหม") on the existing assessment path.
    data_intent = None
    if any(w in lowered for w in ("ความเค็ม", "น้ำเค็ม", "salinity")):
        data_intent = "water_quality"
    elif ("pm2.5" in lowered or "pm 2.5" in lowered or "pm25" in lowered) and any(w in lowered for w in ("สถานี", "ตัวเลข", "ค่าเท่า", "สูงสุด")):
        data_intent = "pm25_stations"
    elif any(w in lowered for w in ("จุดความร้อน", "hotspot")) and any(w in lowered for w in ("กี่", "จำนวน", "เท่าไร", "เท่าไหร่")):
        data_intent = "fire_hotspots"
    elif any(w in lowered for w in ("เรดาร์", "ภาพพยากรณ์", "ภาพฝน", "ภาพพายุ", "ภาพคลื่น", "media", "ลิงก์ภาพ")):
        data_intent = "media"
    elif any(w in lowered for w in ("คลื่น", "wave")) and any(w in lowered for w in ("สูงสุด", "ความสูง", "เท่าไหร่", "กี่", "ตัวเลข", "ข้อมูลคลื่น", "wave")):
        data_intent = "waves"
    elif (any(w in lowered for w in ("พายุอะไร", "พายุไหน", "ชื่อพายุ", "พายุล่าสุด", "พายุที่กำลัง", "active storm", "cyclone"))
          or (not areas
              and any(w in lowered for w in ("พายุหมุน", "พายุโซนร้อน", "ไต้ฝุ่น", "ดีเปรสชัน", "ไซโคลน", "tropical storm", "typhoon"))
              and any(w in lowered for w in ("อะไร", "ไหน", "ชื่อ", "รายชื่อ", "ล่าสุด", "ตอนนี้", "ปัจจุบัน", "active", "current")))):
        data_intent = "storms"
    elif any(w in lowered for w in ("แผ่นดินไหว", "อาฟเตอร์ช็อก", "earthquake", "aftershock", "quake")) and (
            not areas or any(w in lowered for w in ("ล่าสุด", "ที่ไหน", "เกิดเมื่อ", "ขนาด", "magnitude", *QUAKE_LIST_WORDS))):
        data_intent = "earthquake"
    explicit_media = data_intent == "media" and any(w in lowered for w in ("ขอ", "ลิงก์", "ดูภาพ"))
    asks_list = data_intent == "earthquake" and any(w in lowered for w in QUAKE_LIST_WORDS)
    if data_intent and areas and "ไหม" in lowered and detect_hazards(text) and not explicit_media and not asks_list:
        data_intent = None
    if data_intent:
        return Understanding(data_intent, [], areas, start, end, language, regions=regions)
    hazards = detect_hazards(text)
    if not hazards and any(w in lowered for w in OFF_TOPIC_WORDS):
        return Understanding("out_of_scope", [], areas, start, end, language)
    if not hazards and any(w in lowered for w in DAILY_RAIN_WORDS):
        hazards = [HazardType.RAIN_STORM]  # "ตากผ้าได้ไหม" is a rain question about one place
    ranking = any(w in lowered for w in ("จังหวัดไหน", "ที่ไหน", "เสี่ยงสุด", "มากที่สุด", "highest", "most at risk"))
    overview = any(w in lowered for w in ("ทั้งประเทศ", "ทั่วประเทศ", "ภาพรวม", "สรุป", "overview", "nationwide"))
    if MUEANG_QUERY.search(text) and not any(a.level == "amphoe" and a.name_th.startswith("เมือง") for a in areas):
        return Understanding("clarify", hazards, areas, start, end, language, notes=["mueang province required"])
    # Source words must not swallow questions about hazards, areas, dams, or rivers.
    if not hazards and not areas and any(w in lowered for w in SOURCE_WORDS):
        return Understanding("sources", [], [], start, end, language)
    if any(w in lowered for w in PREPARE_WORDS):
        intent = "prepare"
    elif any(w in lowered for w in EXPLAIN_WORDS):
        intent = "explain"
    elif ranking and (not areas or regions) and not future:
        intent = "ranking"
    elif (overview or regions) and (not areas or regions) and not future:
        # Only when asked for the whole country or a region; "วันนี้ฝนตกไหม" means "here", so ask where.
        intent = "overview"
    elif hazards or areas or regions:
        intent = "risk"
    else:
        intent = "out_of_scope"
    notes = []
    if intent != "out_of_scope" and not hazards:
        hazards = list(HazardType)
        notes.append("no hazard named; checking all four")
    if intent in ("risk", "explain") and not areas:
        notes.append("no area found")
    if areas and not regions and sum(1 for a in areas if a.level == areas[0].level and a.name_th == areas[0].name_th) > 1:
        notes.append("ambiguous area")
    return Understanding(intent, hazards, areas, start, end, language, notes, future, regions)
