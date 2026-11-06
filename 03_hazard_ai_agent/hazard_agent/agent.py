"""Hazard agent: understand the question, collect evidence through tools, and answer with sources.

The deterministic path below works without any LLM. An LLM can later replace `understand` for hard
questions and `compose` for wording, but it must keep the same evidence and alert levels.
"""

from collections import Counter
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
import re
from typing import Callable, Protocol

from decision_engine.rag import get_disaster_guidance
from decision_engine.rag.models import format_page_ranges
from geo_integration import Area, Gazetteer
from geo_integration.regions import REGION_CODES
from hazard_agent.understanding import BANGKOK, DAM_HISTORY_WORDS, STATION_CODE, Understanding, detect_hazards, understand
from thanphai_shared import HazardType

LEVEL_TH = {"NORMAL": "ปกติ", "WATCH": "เฝ้าระวัง", "WARNING": "เตือนภัย", "CRITICAL": "วิกฤต", None: "ไม่มีข้อมูลเพียงพอ"}
HAZARD_TH = {"flood": "น้ำท่วม", "rain_storm": "ฝนหนัก/พายุ", "drought": "ภัยแล้ง", "fire": "ไฟป่า/ไฟหญ้า", "air_quality": "ฝุ่น PM2.5", "heat": "ความร้อนจัด", "earthquake": "แผ่นดินไหว", "marine": "คลื่นลมแรง", "tropical_cyclone": "พายุหมุนเขตร้อน", "landslide": "ดินถล่ม"}
SOURCE_TH = {
    "thaiwater_waterlevel": "ThaiWater (สสน.) — ระดับน้ำ",
    "thaiwater_rain_24h": "ThaiWater (สสน.) — ฝนสะสม",
    "thaiwater_dams": "ThaiWater (สสน.) — เขื่อน",
    "thaiwater_waterquality": "ThaiWater (สสน.) — คุณภาพน้ำ",
    "thaiwater_storms": "ThaiWater (สสน.) — พายุ",
    "thaiwater_media": "ThaiWater (สสน.) — ภาพเรดาร์และพยากรณ์",
    "nasa_firms": "NASA FIRMS — จุดความร้อน",
    "air4thai": "กรมควบคุมมลพิษ (Air4Thai) — ฝุ่น PM2.5",
    "tmd_warnings": "กรมอุตุนิยมวิทยา — ประกาศเตือน",
    "tmd_nwp_daily": "กรมอุตุนิยมวิทยา — พยากรณ์ฝน",
    "tmd_earthquakes": "กรมอุตุนิยมวิทยา — แผ่นดินไหว",
    "usgs_earthquakes": "USGS — แผ่นดินไหว",
    "gdacs": "GDACS — เหตุภัยพิบัติ",
    "gdacs_cyclones": "GDACS — พายุหมุนเขตร้อน",
    "gistda_flood": "GISTDA — น้ำท่วมจากดาวเทียม",
    "air_model": "Open-Meteo / CAMS — แบบจำลองฝุ่น",
    "heat_model": "Open-Meteo — ดัชนีความร้อน",
    "marine_model": "Open-Meteo Marine — แบบจำลองคลื่น",
    "open_meteo_waves": "Open-Meteo Marine — ทิศทางคลื่น",
    "fire_model": "แบบจำลองไฟป่าของทันภัย",
}
# Lines of the rule-based answer that are always shown as written, even when the LLM rewrites the rest.
# Answers built from real records that the LLM may reword (validate() keeps every number and level).
# Not media (links must stay exact), prepare (official guidance), emergency, clarify or sources.
# Earthquake lists are not reworded: a rewrite dropped the period and the note on how far the kept TMD history goes.
REWRITE_INTENTS = {"risk", "explain", "overview", "ranking", "dam", "dam_history", "river", "storms",
                   "waves", "water_quality", "pm25_stations", "fire_hotspots"}
VERBATIM_PREFIXES = ("⚠", "ประกาศของกรมอุตุนิยมวิทยาครอบคลุม", "ระดับเตือนภัยตัดสินด้วยกฎ", "หากอยู่ในอันตราย", "ที่มา:")
# Start of a TMD warning line ("⚠ ประกาศกรมอุตุนิยมวิทยา: <title>") once spacing is removed.
WARNING_TITLE = "ประกาศกรมอุตุนิยมวิทยา:"
# Shown with every shelter answer: DDPM's list is a plan, not a notice that a shelter is open now.
SHELTER_CHECK = "⚠ ก่อนเดินทาง โปรดสอบถาม อปท. ในพื้นที่ หรือโทรสายด่วน ปภ. 1784 ว่าศูนย์เปิดรับอยู่"
SHELTER_LIST = 6  # shelters named in one answer; the map shows them all
# The agent has no GPS: a route starts from the visitor's position in the shelter layer ("หาศูนย์ใกล้ฉัน" → "เส้นทาง").
SHELTER_ROUTE_HINT = 'ดูเส้นทางที่เลี่ยงน้ำท่วมจากดาวเทียม: กด "หาศูนย์ใกล้ฉัน" แล้วกด "เส้นทาง" ที่ศูนย์ที่ต้องการ'
MONTH_TH = ("ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค.")


def _date_th(value: str) -> str:
    day = date.fromisoformat(value[:10])
    return f"{day.day} {MONTH_TH[day.month - 1]} {day.year + 543}"


EMERGENCY_TH = "หากอยู่ในอันตราย โทร 1784 (ปภ.) · 1669 (เจ็บป่วยฉุกเฉิน) · 199 (ดับเพลิง) ทันที"
PREPARE_TH = {
    "flood": ["ยกของมีค่าและปลั๊กไฟขึ้นที่สูง", "เตรียมยา น้ำดื่ม อาหารแห้ง ไฟฉาย และเอกสารสำคัญใส่ถุงกันน้ำ", "ติดตามประกาศของ ปภ. และเตรียมเส้นทางอพยพ"],
    "rain_storm": ["หลีกเลี่ยงการเดินทางช่วงฝนตกหนัก", "อยู่ห่างจากต้นไม้ใหญ่และป้ายโฆษณา", "ระวังน้ำป่าไหลหลากหากอยู่ใกล้ภูเขาหรือลำห้วย"],
    "drought": ["กักเก็บน้ำสำรองและใช้น้ำอย่างประหยัด", "ติดตามประกาศพื้นที่ประสบภัยแล้งจาก ปภ.", "วางแผนการเพาะปลูกตามปริมาณน้ำที่มี"],
    "fire": ["งดเผาในที่โล่ง", "ปิดประตูหน้าต่างเมื่อมีควัน และใช้หน้ากาก N95", "แจ้ง 1362 (กรมอุทยานฯ) เมื่อพบไฟป่า"],
    "air_quality": ["ลดกิจกรรมกลางแจ้ง โดยเฉพาะเด็ก ผู้สูงอายุ หญิงตั้งครรภ์ และผู้มีโรคหัวใจหรือทางเดินหายใจ",
                    "สวมหน้ากาก N95 เมื่อออกนอกอาคาร", "ปิดประตูหน้าต่าง และใช้เครื่องฟอกอากาศถ้ามี"],
    "heat": ["ดื่มน้ำบ่อย ๆ แม้ไม่กระหายน้ำ", "เลี่ยงงานหนักกลางแจ้งช่วง 11.00–15.00 น. และพักในที่ร่ม",
             "ดูแลเด็ก ผู้สูงอายุ และผู้มีโรคประจำตัว หากตัวร้อนจัด ซึม หรือหมดสติ โทร 1669"],
    "earthquake": ["ขณะสั่น: หมอบ ป้องกันศีรษะ และหลบใต้โต๊ะที่แข็งแรง อยู่ห่างจากกระจกและของที่อาจตกใส่",
                   "ห้ามใช้ลิฟต์ ออกจากอาคารทางบันไดเมื่อหยุดสั่น", "ระวังอาฟเตอร์ช็อก และตรวจรอยร้าวของอาคารก่อนกลับเข้าไป"],
    "marine": ["เรือเล็กงดออกจากฝั่งเมื่อคลื่นสูง 2 เมตรขึ้นไป", "งดเล่นน้ำทะเลเมื่อมีธงแดงหรือคลื่นแรง",
               "ติดตามประกาศของกรมอุตุนิยมวิทยาและกรมเจ้าท่าก่อนออกเรือ"],
    "tropical_cyclone": ["เก็บหรือยึดสิ่งของนอกบ้านที่ลมพัดปลิวได้ และเตรียมไฟฉาย น้ำดื่ม อาหาร", "เรือทุกขนาดงดออกจากฝั่งในเส้นทางพายุ",
                         "ติดตามประกาศเตือนพายุของกรมอุตุนิยมวิทยาและคำสั่งอพยพของ ปภ."],
    "landslide": ["สังเกตสัญญาณเตือน: น้ำในลำห้วยขุ่นข้นผิดปกติ ดินแยก ต้นไม้เอียง หรือได้ยินเสียงดังจากภูเขา",
                  "เมื่อฝนตกหนักต่อเนื่อง ให้ออกจากบ้านที่อยู่ติดเชิงเขาหรือทางน้ำไหล", "ติดตามประกาศของกรมทรัพยากรธรณีและ ปภ. และเตรียมเส้นทางอพยพ"],
}


def _plain(line: str) -> str:
    """A line without warning marks, bullets or spacing differences, to spot a copied tail line."""
    return re.sub(r"\s+", "", line.replace("⚠", "")).lstrip("•-*").rstrip(".")


def tmd_heat_label(heat_index: float) -> str:
    """TMD/BMA heat-index band (display only; alert levels come from rules.yaml)."""
    if heat_index >= 52:
        return "อันตรายมาก"
    if heat_index >= 42:
        return "อันตราย"
    if heat_index >= 33:
        return "เตือนภัย"
    return "เฝ้าระวัง" if heat_index >= 27 else "ปกติ"


class HazardTools(Protocol):
    """Tools the agent may call. Implemented by the API backend (module 02)."""

    def get_assessments(self, area_code: str) -> list[dict]: ...
    def get_all_assessments(self) -> list[dict]: ...
    def get_official_warnings(self) -> list[dict]: ...
    def get_sources(self) -> dict[str, dict]: ...
    def get_earthquakes(self) -> list[dict]: ...
    def get_quake_gaps(self) -> list[dict]: ...  # optional: spans the kept TMD history may miss
    def get_storms(self) -> list[dict]: ...
    def get_cyclones(self) -> list[dict]: ...
    def get_waves(self) -> list[dict]: ...
    def get_water_quality(self) -> list[dict]: ...
    def get_pm25_stations(self) -> list[dict]: ...
    def get_fire_hotspots(self) -> list[dict]: ...
    def get_media(self) -> dict: ...
    def get_dams(self) -> list[dict]: ...
    def get_dam_history(self, dam_id: str, series: str = "storage") -> dict | None: ...
    def get_shelters(self, area_code: str | None = None) -> list[dict]: ...  # optional: DDPM shelter list
    def shelter_source(self) -> dict | None: ...


@dataclass
class AgentAnswer:
    text: str
    intent: str
    area: dict | None
    hazards: list[str]
    evidence: list[dict] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # True when the LLM wrote `text`; `facts` always holds the rule-based answer it was checked against.
    llm_used: bool = False
    facts: str = ""
    llm_model: str | None = None  # which model wrote `text` when llm_used


# (question, facts) -> rewritten text, or None to keep the rule-based answer.
Explainer = Callable[[str, str], str | None]


def _area_label(area: Area, gazetteer: Gazetteer) -> str:
    prefix = {"province": "จ.", "amphoe": "อ.", "tambon": "ต."}[area.level]
    parents = " ".join({"province": "จ.", "amphoe": "อ."}[p.level] + p.name_th for p in gazetteer.parents(area))
    return f"{prefix}{area.name_th} {parents}".strip()


def _window_th(u: Understanding) -> str:
    s, e = u.window_start, u.window_end
    if e - s <= timedelta(days=1):
        return f"วันที่ {s.day}/{s.month}"
    return f"ช่วง {s.day}/{s.month}–{e.day}/{e.month}"


QUAKE_NEAR_KM = 100
QUAKE_LIST_MAX = 10
QUAKE_PARTIAL_TH = "ข้อมูลยังไม่ครบช่วงที่ถาม"


def _parse_time(value: str | None) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return when.replace(tzinfo=BANGKOK) if when.tzinfo is None else when


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    a = sin(radians(lat2 - lat1) / 2) ** 2 + cos(radians(lat1)) * cos(radians(lat2)) * sin(radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371 * asin(sqrt(a))


def _quake_period(question: str, now: datetime) -> tuple[datetime, datetime, str]:
    """Bounds of the earthquake period asked about, and its Thai label (default: past 7 days)."""
    lowered = question.lower()
    local = now.astimezone(BANGKOK)
    today = local.replace(hour=0, minute=0, second=0, microsecond=0)
    match = re.search(r"(\d+)\s*(วัน|days?)", lowered)
    if match:
        days = min(int(match.group(1)), 62)  # the backend keeps 62 days
        return local - timedelta(days=days), local, f" ย้อนหลัง {days} วัน"
    if "เดือนนี้" in lowered or "this month" in lowered:
        return today.replace(day=1), local, " ตั้งแต่ต้นเดือนนี้"
    if any(w in lowered for w in ("เดือนที่แล้ว", "เดือนก่อน", "last month")):
        first_of_month = today.replace(day=1)
        previous_month = first_of_month - timedelta(days=1)
        return previous_month.replace(day=1), first_of_month, " เดือนที่แล้ว"
    if any(w in lowered for w in ("สัปดาห์นี้", "อาทิตย์นี้", "this week")):
        return today - timedelta(days=today.weekday()), local, " ตั้งแต่ต้นสัปดาห์นี้"
    if any(w in lowered for w in ("สัปดาห์ที่แล้ว", "สัปดาห์ก่อน", "อาทิตย์ที่แล้ว", "อาทิตย์ก่อน", "last week")):
        this_week = today - timedelta(days=today.weekday())
        return this_week - timedelta(days=7), this_week, " สัปดาห์ที่แล้ว"
    if any(w in lowered for w in ("เดือน", "month")):
        return local - timedelta(days=30), local, " ย้อนหลัง 30 วัน"
    if any(w in lowered for w in ("วันนี้", "today")):
        return today, local, " วันนี้"
    if any(w in lowered for w in ("เมื่อวาน", "yesterday")):
        return today - timedelta(days=1), today, " เมื่อวาน"
    return local - timedelta(days=7), local, " ย้อนหลัง 7 วัน"


def _time_th(value: str | None) -> str | None:
    if not value:
        return None
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
        when = when.replace(tzinfo=BANGKOK) if when.tzinfo is None else when.astimezone(BANGKOK)
    except (TypeError, ValueError):
        return None
    return f"{when:%H:%M} น. วันที่ {when.day}/{when.month}/{when.year + 543}"


def _warning_applies(warning: dict, u: Understanding) -> bool:
    """Match each hazard's own province and inclusive impact dates to the question window."""
    province = u.area.code[:4]
    start = u.window_start.date()
    # Question windows are half-open; an impact ending today still includes all of today.
    end = (u.window_end - timedelta(microseconds=1)).date()
    for hazard in {h.value for h in u.hazards} & set(warning.get("hazards", [])):
        if "provinces_by_hazard" in warning:
            provinces = warning["provinces_by_hazard"].get(hazard, [])
        else:
            # Older tools have no scope metadata. Keep their warnings until they supply it.
            provinces = warning.get("provinces", [province])
        if province not in provinces:
            continue
        whole = (warning.get("impact_from"), warning.get("impact_to"))
        first, last = warning.get("impact_by_hazard", {}).get(hazard, {}).get(province, whole)
        if first and date.fromisoformat(first) > end:
            continue
        if last and date.fromisoformat(last) < start:
            continue
        return True
    return False


MCM_PER_DAY_TO_M3S = 1_000_000 / 86_400  # 1 million m³ a day ≈ 11.6 m³ every second


def _flow(mcm_per_day: float) -> str:
    """'5.00 ล้าน ลบ.ม. (ราว 58 ลบ.ม./วินาที)' — daily volume plus the average rate people can picture."""
    return f"{mcm_per_day:,.2f} ล้าน ลบ.ม. (เฉลี่ยราว {mcm_per_day * MCM_PER_DAY_TO_M3S:,.0f} ลบ.ม./วินาที)"


def _dam_line(d: dict) -> str:
    pct = d.get("storage_percent")
    head = f"• เขื่อน{d.get('name_th')} จ.{d.get('province_th')}"
    if pct is None:
        return head + ": ยังไม่มีข้อมูล"
    lines = [f"{head}: {d.get('status_th')} มีน้ำ {pct:.0f}% ของระดับกักเก็บปกติ"
             + (f" ({d['storage_mcm']:,.0f} ล้าน ลบ.ม.)" if d.get("storage_mcm") is not None else "")]
    if d.get("usable_percent") is not None:
        lines.append(f"  น้ำที่นำมาใช้ได้ {d['usable_percent']:.0f}%")
    if d.get("inflow_mcm") is not None:
        lines.append(f"  น้ำไหลเข้าวันนี้ {_flow(d['inflow_mcm'])}")
    if d.get("released_mcm") is not None:
        lines.append(f"  ระบายออกวันนี้ {_flow(d['released_mcm'])}")
    if d.get("spilled_mcm"):
        lines.append(f"  ระบายผ่านทางน้ำล้น {d['spilled_mcm']:,.2f} ล้าน ลบ.ม.")
    return "\n".join(lines)


AGENCY_TH = {"RID": "กรมชลประทาน", "EGAT": "กฟผ.", "HII": "สสน.", "DWR": "กรมทรัพยากรน้ำ"}
# The Chao Phraya Dam (Chai Nat) is a diversion dam with no reservoir, so it is not among the 35 large dams.
# What it releases is the flow measured just below it, at RID station C.13.
CHAO_PHRAYA_DAM_STATION = "C.13"


def _river_line(r: dict) -> str:
    head = f"• {r.get('code')} {r.get('name_th') or ''}".rstrip() + (f" ({r['river_th']})" if r.get("river_th") else "")
    head += f" จ.{r.get('province_th')}" if r.get("province_th") else ""
    parts = []
    if r.get("discharge_m3s") is not None:
        flow = f"น้ำไหลผ่าน {r['discharge_m3s']:,.0f} ลบ.ม./วินาที"
        if r.get("capacity_percent") is not None:
            flow += f" ({r['capacity_percent']}% ของความจุลำน้ำ)"
        parts.append(flow)
    above = r.get("above_bank_m")
    if above is not None:
        parts.append(f"ล้นตลิ่ง {above:.2f} ม." if above > 0 else f"ต่ำกว่าตลิ่ง {-above:.2f} ม.")
    change = r.get("level_change_m")
    if change is not None:
        parts.append("ระดับน้ำทรงตัว" if abs(change) < 0.01 else f"ระดับน้ำ{'เพิ่มขึ้น' if change > 0 else 'ลดลง'} {abs(change) * 100:.0f} ซม. จากชั่วโมงก่อน")
    return f"{head}: " + " · ".join(parts) if parts else head


def _reason(item: dict) -> str:
    obs = item.get("observations") or {}
    parts = []
    if obs.get("above_bank_m") is not None:
        v = obs["above_bank_m"]
        parts.append(f"ระดับน้ำ{'สูงกว่า' if v > 0 else 'ต่ำกว่า'}ตลิ่ง {abs(v):.2f} ม.")
    cells = obs.get("satellite_flood_cells_7d")
    if cells:
        # Cells that contain some flooded area (~half of each cell is water), so this is the spread, not the water area.
        parts.append(f"ดาวเทียมพบน้ำท่วมกระจายในพื้นที่ราว {cells * 0.1245:.0f} ตร.กม. ใน 7 วัน")
    if obs.get("rain_24h") is not None:
        parts.append(f"ฝนสะสม 24 ชม. สูงสุด {obs['rain_24h']:.1f} มม.")
    if obs.get("rain_chance_now") is not None:
        parts.append(f"โอกาสฝนตกชั่วโมงนี้ {obs['rain_chance_now']:.0f}%")
    if obs.get("rain_chance_max_24h") is not None:
        at = f" ราว {obs['rain_chance_peak_hour']:02.0f}:00 น." if obs.get("rain_chance_peak_hour") is not None else ""
        parts.append(f"โอกาสฝนตกสูงสุดใน 24 ชม. ข้างหน้า {obs['rain_chance_max_24h']:.0f}%{at} (แบบจำลอง Open-Meteo)")
    if obs.get("hotspots_24h") is not None:
        parts.append(f"จุดความร้อน 24 ชม. {int(obs['hotspots_24h'])} จุด")
    if obs.get("fire_probability_7d") is not None:
        parts.append(f"แบบจำลองประเมินโอกาสเกิดไฟใน 7 วัน {obs['fire_probability_7d'] * 100:.0f}%")
    if obs.get("pm25_24h") is not None:
        parts.append(f"PM2.5 เฉลี่ย 24 ชม. {obs['pm25_24h']:.1f} มคก./ลบ.ม. (สถานีกรมควบคุมมลพิษ)")
    if obs.get("pm25_model_24h") is not None:
        parts.append(f"แบบจำลองคาดการณ์ PM2.5 เฉลี่ย 24 ชม. ข้างหน้า {obs['pm25_model_24h']:.1f} มคก./ลบ.ม.")
    if obs.get("heat_index_now") is not None:
        # "ตอนนี้กี่องศา" asks about now: the forecast for this hour comes before the 24-h peak.
        parts.append(f"ตอนนี้ร้อนเหมือน {obs['heat_index_now']:.1f}°C (ดัชนีความร้อน ค่าคาดการณ์ชั่วโมงนี้)")
    if obs.get("heat_index_max_24h") is not None:
        hi = obs["heat_index_max_24h"]
        parts.append(f"ดัชนีความร้อนสูงสุดใน 24 ชม. ข้างหน้า {hi:.1f}°C (เกณฑ์กรมอุตุฯ: {tmd_heat_label(hi)})")
    if obs.get("quake_mmi_24h") is not None:
        if obs.get("quake_magnitude") is not None:
            parts.append(f"แผ่นดินไหวขนาด {obs['quake_magnitude']:.1f} ห่างราว {obs['quake_distance_km']:.0f} กม. "
                         f"ประเมินแรงสั่นที่นี่ระดับ {obs['quake_mmi_24h']:.0f} (มาตราเมอร์คัลลี)")
        else:
            # Not "no earthquake": small quakes far away are left out, so say what was checked.
            parts.append("ไม่มีแผ่นดินไหวใกล้พื้นที่นี้หรือที่ส่งแรงสั่นถึงที่นี่ใน 24 ชม. ที่ผ่านมา")
    if obs.get("wave_height_max_24h") is not None:
        parts.append(f"คลื่นสูงสุดใน 24 ชม. ข้างหน้าราว {obs['wave_height_max_24h']:.1f} เมตร")
    if obs.get("cyclone_wind_kmh"):
        parts.append(f"อยู่ในรัศมีลมแรงของพายุ {obs['cyclone_wind_kmh']:.0f} กม./ชม. ขึ้นไป (รวมเส้นทางคาดการณ์)")
    if obs.get("cyclone_distance_km") is not None:
        parts.append(f"พายุหมุนเขตร้อนเข้าใกล้ที่สุดราว {obs['cyclone_distance_km']:.0f} กม.")
    if obs.get("landslide_traces") is not None:
        parts.append(f"พื้นที่เคยเกิดดินถล่ม (พบร่องรอย {obs['landslide_traces']:.0f} จุด)")
        if obs.get("landslide_rain_24h") is not None:
            parts.append(f"ฝน 24 ชม. สูงสุด {obs['landslide_rain_24h']:.1f} มม.")
        if obs.get("landslide_forecast_rain_24h") is not None:
            parts.append(f"พยากรณ์ฝนรายวันสูงสุด {obs['landslide_forecast_rain_24h']:.1f} มม.")
    if obs.get("cross_border_hotspots_24h"):
        parts.append(f"จุดความร้อนในประเทศเพื่อนบ้านในรัศมี 100 กม. {int(obs['cross_border_hotspots_24h'])} จุด (24 ชม.)")
    if obs.get("vci") is not None:
        parts.append(f"ดัชนีความสมบูรณ์ของพืช (VCI) {obs['vci']:.0f} จาก 100 เทียบเดือนเดียวกันในอดีต")
    elif obs.get("ndvi_deficit") is not None:
        parts.append("พืชพรรณเขียวใกล้เคียงปกติของเดือนนี้" if obs["ndvi_deficit"] < 0.05 else "พืชพรรณเขียวน้อยกว่าปกติ")
    if obs.get("spi_3") is not None:
        parts.append(f"ดัชนี SPI-3 {obs['spi_3']:+.2f}")
    if "OFFICIAL_WARNING" in (item.get("rule_ids") or []):
        parts.append("มีประกาศเตือนจากกรมอุตุนิยมวิทยา")
    elif "OFFICIAL_WARNING_VERY_HEAVY_RAIN" in (item.get("rule_ids") or []):
        parts.append("ประกาศกรมอุตุนิยมวิทยาคาดว่าจังหวัดนี้มีฝนตกหนักมาก")
    elif "OFFICIAL_WARNING_HEAVY_RAIN" in (item.get("rule_ids") or []):
        parts.append("ประกาศกรมอุตุนิยมวิทยาคาดว่าจังหวัดนี้มีฝนตกหนัก")
    elif "OFFICIAL_WARNING_REGIONAL" in (item.get("rule_ids") or []):
        parts.append("มีประกาศเตือนจากกรมอุตุนิยมวิทยาครอบคลุมทั้งภาค (ไม่ได้ระบุจังหวัดนี้โดยตรง)")
    elif "OFFICIAL_WARNING_UPCOMING" in (item.get("rule_ids") or []):
        parts.append("มีประกาศเตือนล่วงหน้าจากกรมอุตุนิยมวิทยา (ยังไม่ถึงช่วงที่มีผลกระทบ)")
    return ", ".join(parts)


class HazardAgent:
    def __init__(self, gazetteer: Gazetteer, tools: HazardTools, explainer: Explainer | None = None):
        self.gazetteer = gazetteer
        self.tools = tools
        self.explainer = explainer

    def _model_name(self) -> str | None:
        return getattr(getattr(self.explainer, "config", None), "model", None)

    def _chat(self, question: str, fallback: AgentAnswer) -> AgentAnswer:
        """Small talk in the assistant's own voice; the fixed text when no LLM answers safely."""
        chat = getattr(self.explainer, "chat", None)
        reply = chat(question) if chat else None
        if not reply:
            return fallback
        return AgentAnswer(reply.strip(), fallback.intent, None, [], llm_used=True, facts=fallback.text,
                           llm_model=self._model_name())

    def ask(self, question: str, now: datetime, area_code: str | None = None) -> AgentAnswer:
        """area_code: the area the user has open (picked on the map or named in an earlier question).
        It is used only when the question names no place, so "แล้วฝนตกไหม" follows the conversation."""
        answer = self._route(question, now, area_code)
        if self.explainer and answer.intent in REWRITE_INTENTS and answer.text and not answer.llm_used:
            answer.facts = answer.text
            rewritten = self.explainer(question, answer.text)
            if rewritten:
                # The LLM writes only the summary; validate() has checked every number and level against the facts.
                # Official warnings, emergency numbers and sources are copied from the rule-based answer word for
                # word, so a rewrite can never drop or change them.
                tail = [line for line in answer.text.splitlines() if line.startswith(VERBATIM_PREFIXES)]
                # The model sometimes copies a tail line ("ที่มา: …") into its own text; keep only the verbatim copy.
                # It also copies a warning without its "⚠", with other spacing or with a shortened title; a TMD title
                # is shown only verbatim, so any warning line of its own is dropped.
                copied = {_plain(line) for line in tail}
                body = [line for line in rewritten.strip().splitlines()
                        if not line.strip().lstrip("•-* ").startswith(VERBATIM_PREFIXES)
                        and _plain(line) not in copied and not _plain(line).startswith(WARNING_TITLE)]
                answer.text, answer.llm_used = "\n".join([*body, *tail]), True
                answer.llm_model = self._model_name()
        return answer

    def _route(self, question: str, now: datetime, area_code: str | None = None) -> AgentAnswer:
        # "พรุ่งนี้" means tomorrow in Thailand; with a UTC clock it would be wrong between 00:00 and 07:00.
        u = understand(question, self.gazetteer, now.astimezone(BANGKOK))
        if u.intent in ("risk", "out_of_scope", "clarify") and self._names_a_dam(question, u):
            # "ภูมิพล" or "สิริกิติ์ตอนนี้น้ำเท่าไหร่" without the word "เขื่อน"; a comparison goes to the
            # history, as it does with "เขื่อน" ("หนองปลาไหลเทียบกับเมื่อวาน").
            if any(w in question.lower() for w in DAM_HISTORY_WORDS):
                return self._data_answer(question, replace(u, intent="dam_history"), now.astimezone(BANGKOK))
            return self._dams(question, u)
        context = self.gazetteer.by_code.get(area_code) if area_code else None
        if context is not None and u.intent in ("risk", "explain") and not u.areas and not u.regions:
            # A question about "here" with an area already open: answer for that area instead of asking where.
            u.areas = [context]
            u.notes = [n for n in u.notes if n != "no area found"] + ["area from the open area"]
        if u.intent == "emergency":
            return AgentAnswer(EMERGENCY_TH, u.intent, None, [h.value for h in u.hazards])
        if u.intent == "greeting":
            text = ("สวัสดีครับ ทันภัยช่วยติดตามภัยในประเทศไทย ลองถามได้ เช่น\n"
                    "• ตอนนี้อำเภอเสนาน้ำท่วมไหม\n"
                    "• พรุ่งนี้ฝนตกที่จังหวัดขอนแก่นไหม\n"
                    "• ต้องเตรียมตัวอย่างไรเมื่อเกิดไฟป่า")
            return self._chat(question, AgentAnswer(text, u.intent, None, []))
        if "mueang province required" in u.notes:
            text = ("อำเภอเมืองของจังหวัดไหนครับ กรุณาระบุจังหวัด เช่น อำเภอเมืองเชียงใหม่"
                    if not any(a.level == "province" for a in u.areas)
                    else "ไม่พบอำเภอเมืองในพื้นที่ที่ระบุ กรุณาระบุชื่ออำเภอและจังหวัดครับ")
            return AgentAnswer(text, "clarify", None, [h.value for h in u.hazards], notes=u.notes)
        data_intents = {"earthquake", "storms", "waves", "water_quality", "pm25_stations", "fire_hotspots", "dam_history", "media"}
        if u.intent in data_intents:
            return self._data_answer(question, u, now.astimezone(BANGKOK))
        if u.regions and u.intent not in ("overview", "ranking"):
            text = (f"คำถามนี้ครอบคลุม{' / '.join(u.regions)} ทันภัยยังไม่สรุปข้อมูลทั้งภาคในคำตอบนี้ "
                    "กรุณาระบุจังหวัดหรืออำเภอที่ต้องการครับ")
            return AgentAnswer(text, "clarify", None, [h.value for h in u.hazards])
        if u.intent == "sources":
            return self._sources()
        if u.intent == "shelter":
            if context is not None and not u.areas and not u.regions:
                u.areas = [context]
            return self._shelters(u)
        if u.intent == "dam":
            return self._dams(question, u)
        if u.intent == "river":
            return self._rivers(question, u)
        if u.intent == "out_of_scope" and u.area:
            # Off topic but about a place: say what ThanPhai can tell about that place instead of a hazard report.
            label = _area_label(u.area, self.gazetteer)
            text = (f"เรื่องร้านอาหาร ที่เที่ยว หรือที่พัก น้องทันภัยแนะนำไม่ได้ครับ แต่ช่วยดูภัยที่{label}ให้ได้ ลองถามเช่น\n"
                    f"• {label} วันนี้ฝนตกไหม\n"
                    f"• {label} ตอนนี้มีภัยอะไรบ้าง\n"
                    f"เปิด{label}บนแผนที่ให้แล้ว ดูระดับภัยแต่ละเรื่องได้ทันทีครับ")
            area_info = {"code": u.area.code, "level": u.area.level, "label": label, "lat": u.area.lat, "lon": u.area.lon}
            return AgentAnswer(text, u.intent, area_info, [])
        if u.intent == "out_of_scope":
            text = (f"ทันภัยตอบได้เฉพาะเรื่อง{' · '.join(HAZARD_TH.values())}ในประเทศไทยครับ "
                    'ลองถามเช่น "น้ำจะท่วมอำเภอบางบัวทองไหม"')
            return self._chat(question, AgentAnswer(text, u.intent, None, []))
        if u.intent in ("overview", "ranking"):
            return self._national(question, u)
        if u.intent == "prepare" and not u.area:
            return self._prepare(question, u, [])
        if not u.area:
            # Still a question ThanPhai can answer once it knows where: ask back instead of guessing the country.
            about = f"เรื่อง{HAZARD_TH[u.hazards[0].value]}" if len(u.hazards) == 1 else ""
            text = (f"ไม่พบชื่อพื้นที่ในคำถาม อยากให้น้องทันภัยดู{about}ที่อำเภอหรือจังหวัดไหนครับ "
                    "พิมพ์ชื่อพื้นที่มาได้เลย เช่น \"วันนี้ตากผ้าที่อำเภอเสนาได้ไหม\" "
                    "หรือเลือกพื้นที่บนแผนที่ก่อนแล้วถามใหม่ครับ")
            return AgentAnswer(text, "clarify", None, [h.value for h in u.hazards], notes=[*u.notes, "area required"])
        if "ambiguous area" in u.notes:
            same_name = [a for a in u.areas if a.level == u.area.level and a.name_th == u.area.name_th]
            options = " / ".join(_area_label(a, self.gazetteer) for a in same_name[:4])
            prompt = f"ยังมีอีก {len(same_name) - 4} แห่ง ช่วยระบุจังหวัดที่ต้องการครับ" if len(same_name) > 4 else "หมายถึงที่ไหนครับ"
            return AgentAnswer(f"พบหลายพื้นที่ชื่อนี้: {options} {prompt}", "clarify", None, [h.value for h in u.hazards], notes=u.notes)
        return self._assess(u, question)

    def _data_answer(self, question: str, u: Understanding, now: datetime) -> AgentAnswer:
        """Answer data questions from backend records without asking the LLM to supply facts."""
        intent = u.intent
        calls: list[str] = []

        def rows(method: str) -> list[dict]:
            fn = getattr(self.tools, method, None)
            if not fn:
                return []
            calls.append(f"{method}()")
            value = fn()
            return value if isinstance(value, list) else []

        def sources_down(*names: str) -> bool:
            get = getattr(self.tools, "get_sources", None)
            if not get:
                return False
            calls.append("get_sources()")
            statuses = get()
            found = [statuses[name] for name in names if name in statuses]
            return any(state.get("ok") is False for state in found)

        def finish(lines: list[str], evidence: list[dict], sources: list[str]) -> AgentAnswer:
            if sources:
                lines.append("ที่มา: " + ", ".join(dict.fromkeys(sources)))
            return AgentAnswer("\n".join(lines), intent, None, [], evidence=evidence,
                               sources=list(dict.fromkeys(sources)), tool_calls=calls)

        def station_scope(stations: list[dict]) -> tuple[list[dict], str]:
            if u.regions:
                wanted = {code for region in u.regions for code in REGION_CODES[region]}
                label = " / ".join(u.regions)
            elif u.areas:
                wanted = {area.code[:4] for area in u.areas}
                names = [self.gazetteer.by_code[code].name_th for code in sorted(wanted)]
                label = "จังหวัด" + " / ".join(names) + " (สรุประดับจังหวัด)"
            else:
                return stations, "ทั่วประเทศ"

            def province(station: dict) -> str | None:
                if station.get("province_code"):
                    return station["province_code"]
                # Air4Thai supplies administrative names in area_th, but no province code.
                text = station.get("province_th") or (station.get("area_th") or "").rsplit(",", 1)[-1]
                # Road/park names may name another province; use the final administrative part.
                text = re.split(r"จังหวัด|จ\.|อำเภอ|อ\.|เขต|ตำบล|ต\.|แขวง", text)[-1]
                codes = {area.code[:4] for area in self.gazetteer.find_in_text(text)}
                return next(iter(codes)) if len(codes) == 1 else None

            return [station for station in stations if province(station) in wanted], label

        if intent == "earthquake":
            since, until, period = _quake_period(question, now)
            all_quakes = rows("get_earthquakes")
            quakes = [q for q in all_quakes if since <= (_parse_time(q.get("time")) or since) < until]
            where = ""
            if u.areas:
                # Province or district centre: events within QUAKE_NEAR_KM of it (a province is up to ~150 km across).
                area = u.areas[0]
                near = []
                for q in quakes:
                    if q.get("lat") is None or q.get("lon") is None:
                        continue
                    km = _km(area.lat, area.lon, q["lat"], q["lon"])
                    if km <= QUAKE_NEAR_KM:
                        near.append({**q, "distance_km": round(km)})
                quakes, where = near, f"ใกล้{_area_label(area, self.gazetteer)} (ภายใน {QUAKE_NEAR_KM} กม.)"
            total = len(quakes)
            quakes = quakes[:QUAKE_LIST_MAX if u.areas else 5]
            # TMD lists only its last 10 events, so ThanPhai keeps what it has seen (the backend keeps 62 days and
            # fetches USGS for the same span, so USGS covers every period asked about). The count is partial when
            # the kept TMD history starts after the period begins, or when the period overlaps a span the backend
            # may have missed (TMD or ThanPhai down): say so and give the dates.
            tmd_times = [t for q in all_quakes if q.get("source") == "กรมอุตุนิยมวิทยา" and (t := _parse_time(q.get("time")))]
            kept_from = min(tmd_times) if tmd_times else None
            missed = [(s, e) for g in rows("get_quake_gaps")
                      if (s := _parse_time(g.get("start"))) and (e := _parse_time(g.get("end"))) and s < until and e > since]
            usgs_only = "ช่วงนั้นมีเฉพาะแผ่นดินไหวขนาด 3.5 ขึ้นไปจาก USGS จำนวนที่แสดงจึงอาจน้อยกว่าจริง"
            gap = None
            if kept_from is None or kept_from > since:
                tmd_part = (f"ข้อมูลกรมอุตุฯ ที่ทันภัยเก็บไว้เริ่มตั้งแต่ {_time_th(kept_from.isoformat())}"
                            if kept_from is not None else "ยังไม่มีข้อมูลกรมอุตุฯ ที่ทันภัยเก็บไว้")
                gap = f"{QUAKE_PARTIAL_TH}: {tmd_part} {usgs_only.replace('ช่วงนั้น', 'ช่วงก่อนหน้านั้น')}"
            elif missed:
                spans = ", ".join(f"{_time_th(s.isoformat())} ถึง {_time_th(e.isoformat())}" for s, e in missed)
                gap = f"{QUAKE_PARTIAL_TH}: ข้อมูลกรมอุตุฯ อาจขาดช่วง {spans} {usgs_only}"
            if not quakes:
                message = ("แหล่งข้อมูลแผ่นดินไหวขัดข้อง จึงยังตรวจสอบรายการล่าสุดไม่ได้"
                           if sources_down("tmd_earthquakes", "usgs_earthquakes")
                           else f"ไม่พบแผ่นดินไหว{where or 'ในข้อมูล'}{period}จาก TMD/USGS")
                return finish([message] + ([gap] if gap else []), [], ["กรมอุตุนิยมวิทยา (TMD)", "USGS"])
            lines = [f"แผ่นดินไหว{where}{period}: {'อย่างน้อย ' if gap else ''}{total} ครั้ง"
                     + (f" (แสดง {len(quakes)} ครั้งล่าสุด)" if total > len(quakes) else "")
                     if u.areas else f"แผ่นดินไหวล่าสุด{period}:"]
            if sources_down("tmd_earthquakes", "usgs_earthquakes"):
                lines.append("แหล่งข้อมูลแผ่นดินไหวบางแห่งขัดข้อง รายการอาจไม่ครบ")
            sources = []
            for q in quakes:
                magnitude = q.get("magnitude")
                place = q.get("place_th") or q.get("where_th") or q.get("place") or "ไม่ระบุสถานที่"
                time = _time_th(q.get("time"))
                source = q.get("source") or "TMD/USGS"
                sources.append(source)
                lines.append(f"• ขนาด {magnitude:g}" if isinstance(magnitude, (int, float)) else "• ไม่ระบุขนาด")
                lines[-1] += f" · {place} · {time or 'เวลาไม่ระบุ'}"
                if q.get("distance_km") is not None:
                    lines[-1] += f" · ห่างราว {q['distance_km']} กม."
            if gap:
                lines.append(gap)
            return finish(lines, quakes, sources)

        if intent == "storms":
            storms, cyclones = rows("get_storms"), rows("get_cyclones")
            records = [(s, "ThaiWater (สสน.)") for s in storms] + [(c, "GDACS") for c in cyclones]
            if not records:
                message = ("แหล่งข้อมูลพายุขัดข้อง จึงยังตรวจสอบรายการล่าสุดไม่ได้"
                           if sources_down("thaiwater_storms", "gdacs_cyclones")
                           else "ไม่พบรายการพายุที่กำลังติดตามในข้อมูลล่าสุด")
                return finish([message], [], ["ThaiWater (สสน.)", "GDACS"])
            lines, evidence = ["พายุที่พบในข้อมูลติดตามล่าสุด:"], []
            if sources_down("thaiwater_storms", "gdacs_cyclones"):
                lines.append("แหล่งข้อมูลพายุบางแห่งขัดข้อง รายการอาจไม่ครบ")
            for row, source in records[:8]:
                props = row.get("properties", row)
                name = props.get("name") or props.get("event") or props.get("event_id") or "ไม่ระบุชื่อ"
                wind = props.get("wind_kmh")
                time = _time_th(props.get("time") or row.get("time"))
                detail = f" · ลม {wind:g} กม./ชม." if isinstance(wind, (int, float)) else ""
                if source == "GDACS":
                    threshold = props.get("wind_threshold_kmh")
                    detail = (f" · ขอบเขตลมตั้งแต่ {threshold:g} กม./ชม. (รวมแนวคาดการณ์)"
                              if isinstance(threshold, (int, float)) else "")
                    time = f"อัปเดตข้อมูล {time}" if time else None
                lines.append(f"• {name}{detail} · {time or 'เวลาไม่ระบุ'}")
                evidence.append(row)
            return finish(lines, evidence, [source for _, source in records])

        if intent == "waves":
            waves = rows("get_waves")
            waves.sort(key=lambda row: row.get("height_m") or 0, reverse=True)
            if not waves:
                message = ("แบบจำลองคลื่นขัดข้อง จึงยังดึงข้อมูลล่าสุดไม่ได้"
                           if sources_down("open_meteo_waves") else "ยังไม่มีข้อมูลคลื่นจากแบบจำลอง Open-Meteo Marine")
                return finish([message], [], ["Open-Meteo Marine"])
            lines = ["ค่าคลื่นสูงสุดที่มีข้อมูลใน 24 ชั่วโมงข้างหน้า:"]
            for wave in waves[:5]:
                height = wave.get("height_m")
                time = _time_th(wave.get("peak_time") or wave.get("time"))
                where = f"พิกัด {wave['lat']:.2f}, {wave['lon']:.2f}" if wave.get("lat") is not None and wave.get("lon") is not None else ""
                lines.append((f"• {height:g} เมตร" if isinstance(height, (int, float)) else "• ไม่มีค่าคลื่น")
                             + (f" · {where}" if where else "") + f" · ช่วงสูงสุด {time or 'เวลาไม่ระบุ'}")
            return finish(lines, waves[:5], ["Open-Meteo Marine"])

        if intent == "water_quality":
            stations, scope = station_scope(rows("get_water_quality"))
            stations.sort(key=lambda row: row.get("salinity_gl") or 0, reverse=True)
            if not stations:
                message = ("แหล่งข้อมูลคุณภาพน้ำขัดข้อง จึงยังตรวจสอบค่าความเค็มล่าสุดไม่ได้"
                           if sources_down("thaiwater_waterquality") else f"ไม่พบสถานีคุณภาพน้ำที่มีข้อมูลและระบุจังหวัดได้ใน{scope}")
                return finish([message], [], ["คลังข้อมูลน้ำแห่งชาติ (สสน.)"])
            lines = [f"สถานีที่มีค่าความเค็มสูงสุด — {scope}:"]
            for station in stations[:5]:
                value = station.get("salinity_gl")
                time = _time_th(station.get("time"))
                name = station.get("name_th") or "ไม่ระบุชื่อสถานี"
                province = station.get("province_th")
                agency = station.get("agency_th")
                lines.append((f"• {name}" + (f" จ.{province}" if province else "")
                              + (f": {value:g} กรัม/ลิตร" if isinstance(value, (int, float)) else ": ไม่มีค่าความเค็ม")
                              + (f" · {agency}" if agency else "") + f" · {time or 'เวลาไม่ระบุ'}"))
            return finish(lines, stations[:5], ["คลังข้อมูลน้ำแห่งชาติ (สสน.)", "กรมควบคุมมลพิษ", "การประปานครหลวง"])

        if intent == "pm25_stations":
            stations, scope = station_scope(rows("get_pm25_stations"))
            stations.sort(key=lambda row: row.get("pm25") or 0, reverse=True)
            if not stations:
                message = ("แหล่งข้อมูล Air4Thai ขัดข้อง จึงยังตรวจสอบค่า PM2.5 ล่าสุดไม่ได้"
                           if sources_down("air4thai") else f"ไม่พบสถานี Air4Thai ที่มีข้อมูลและระบุจังหวัดได้ใน{scope}")
                return finish([message], [], ["กรมควบคุมมลพิษ (Air4Thai)"])
            lines = [f"สถานีที่มีค่า PM2.5 สูงสุด — {scope}:"]
            for station in stations[:5]:
                value = station.get("pm25")
                where = station.get("area_th")
                time = _time_th(station.get("time"))
                lines.append((f"• {station.get('name_th') or 'ไม่ระบุชื่อสถานี'}"
                              + (f" ({where})" if where else "")
                              + (f": {value:g} มคก./ลบ.ม." if isinstance(value, (int, float)) else ": ไม่มีค่า PM2.5")
                              + f" · {time or 'เวลาไม่ระบุ'}"))
            return finish(lines, stations[:5], ["กรมควบคุมมลพิษ (Air4Thai)"])

        if intent == "fire_hotspots":
            hotspots = rows("get_fire_hotspots")
            wanted_provinces: set[str] | None = None
            label = "ประเทศไทย"
            if u.regions:
                label = " / ".join(u.regions)
                wanted_provinces = {code for region in u.regions for code in REGION_CODES.get(region, ())}
            elif u.areas:
                area = u.areas[0]
                province = area.code[:4] if area.level != "province" else area.code
                wanted_provinces = {province}
                province_area = self.gazetteer.by_code.get(province)
                label = province_area.name_th if province_area else area.name_th
            selected = [row for row in hotspots if wanted_provinces is None or row.get("area_code", "")[:4] in wanted_provinces]
            if not selected:
                message = (f"แหล่งข้อมูล FIRMS ขัดข้อง จึงยังนับจุดความร้อนใน{label}ไม่ได้"
                           if sources_down("nasa_firms") else f"ไม่มีรายการจุดความร้อนในข้อมูล FIRMS สำหรับ{label}")
                return finish([message], [], ["NASA FIRMS"])
            count = sum(row.get("hotspots_24h") or 0 for row in selected)
            latest = max((row.get("time") for row in selected if row.get("time")), default=None)
            time = _time_th(latest)
            lines = [f"พบจุดความร้อนใน{label} {count:g} จุดใน 24 ชั่วโมงล่าสุด (ข้อมูล {time or 'เวลาไม่ระบุ'})"]
            return finish(lines, selected, ["NASA FIRMS"])

        if intent == "dam_history":
            dams = rows("get_dams")
            lowered = question.lower()
            named = [d for d in sorted(dams, key=lambda d: -len(d.get("name_th") or ""))
                     if (d.get("name_th") and d["name_th"] in question)
                     or (d.get("name_en") and d["name_en"].lower() in lowered)]
            if not named:
                return AgentAnswer("กรุณาระบุชื่อเขื่อนที่ต้องการเทียบข้อมูลย้อนหลังครับ", "clarify", None, [],
                                   tool_calls=calls, notes=["dam name required"])
            dam = named[0]
            get_history = getattr(self.tools, "get_dam_history", None)
            if get_history is None:
                return finish(["ยังดึงประวัติเขื่อนจาก ThaiWater ไม่ได้ในขณะนี้"], [], ["ThaiWater (สสน.)"])
            series = "storage"
            if any(w in lowered for w in ("ปล่อย", "ระบาย", "released", "release")):
                series = "released"
            elif any(w in lowered for w in ("ไหลเข้า", "น้ำเข้า", "inflow")):
                series = "inflow"
            metric = {"storage": "ปริมาณน้ำกักเก็บ", "released": "ปริมาณน้ำระบาย", "inflow": "ปริมาณน้ำไหลเข้า"}[series]
            calls.append(f"get_dam_history({dam['id']}, {series})")
            try:
                history = get_history(dam["id"], series)
            except Exception:
                return finish([f"ไม่สามารถดึงประวัติเขื่อน{dam['name_th']}จาก ThaiWater ได้ในขณะนี้"], [], ["ThaiWater (สสน.)"])
            values = [tuple(item) for year in (history or {}).get("series", []) for item in year.get("values", [])]
            parsed = sorted(((date.fromisoformat(day), value) for day, value in values if day and value is not None), key=lambda item: item[0])
            if not parsed:
                return finish([f"ยังไม่มีข้อมูลย้อนหลังของเขื่อน{dam['name_th']} ให้เปรียบเทียบ"], [], ["ThaiWater (สสน.)"])
            if "เมื่อวาน" in question and not any(w in question for w in ("เทียบ", "เปรียบเทียบ")):
                day = now.date() - timedelta(days=1)
                value = next((value for record_day, value in parsed if record_day == day), None)
                text = (f"เขื่อน{dam['name_th']} {metric}: {value:,.0f} ล้าน ลบ.ม. ณ {day.day}/{day.month}/{day.year + 543}"
                        if value is not None else f"ไม่มีข้อมูล{metric}ของเขื่อน{dam['name_th']} วันที่ {day.day}/{day.month}/{day.year + 543}")
                return finish([text], history.get("series", []), ["ThaiWater (สสน.)"])
            current = next((item for item in reversed(parsed) if item[0] <= now.date()), None)
            comparison_day = now.date() - timedelta(days=7 if any(w in question for w in ("สัปดาห์", "อาทิตย์")) else 1)
            previous = (min((item for item in parsed if item[0] < current[0] and abs((item[0] - comparison_day).days) <= 1),
                            key=lambda item: abs((item[0] - comparison_day).days), default=None)
                        if current else None)
            if not current or not previous:
                return finish([f"ข้อมูลย้อนหลังของเขื่อน{dam['name_th']} ยังไม่ครบพอสำหรับเทียบกับวันที่ {comparison_day.day}/{comparison_day.month}"],
                              history.get("series", []) if history else [], ["ThaiWater (สสน.)"])
            delta = current[1] - previous[1]
            trend = "เพิ่มขึ้น" if delta > 0 else "ลดลง" if delta < 0 else "เท่าเดิม"
            lines = [f"เขื่อน{dam['name_th']} {metric}: {current[1]:,.0f} ล้าน ลบ.ม. ณ {current[0].day}/{current[0].month}/{current[0].year + 543}",
                     f"เทียบกับ {previous[0].day}/{previous[0].month}/{previous[0].year + 543}: {previous[1]:,.0f} ล้าน ลบ.ม. · {trend} {abs(delta):,.0f} ล้าน ลบ.ม."]
            return finish(lines, history.get("series", []) if history else [], ["ThaiWater (สสน.)"])

        if intent == "media":
            fn = getattr(self.tools, "get_media", None)
            media = fn() if fn else {}
            if fn:
                calls.append("get_media()")
            groups = [("เรดาร์ฝน", media.get("radar", [])), ("ภาพพยากรณ์ฝน", media.get("rain_forecast", {})),
                      ("ภาพพายุ", media.get("storm", [])), ("ภาพคลื่น", media.get("wave", []))]
            requested = [label for label, words in (("เรดาร์ฝน", ("เรดาร์", "radar")),
                         ("ภาพพยากรณ์ฝน", ("พยากรณ์", "ภาพฝน", "forecast")),
                         ("ภาพพายุ", ("พายุ", "storm")), ("ภาพคลื่น", ("คลื่น", "wave")))
                         if any(word in question.lower() for word in words)]
            if requested:
                groups = [(label, group) for label, group in groups if label in requested]
            links = []
            for label, group in groups:
                if isinstance(group, list):
                    items = group
                elif isinstance(group, dict) and "image" in group:
                    items = [group]
                elif isinstance(group, dict):
                    items = [image for section in group.values() if isinstance(section, dict)
                             for image in section.get("days", []) if isinstance(image, dict)]
                else:
                    items = []
                links.extend((label, item) for item in items if item and item.get("image"))
            if not links:
                return finish(["ยังไม่มีลิงก์ภาพเรดาร์หรือภาพพยากรณ์ล่าสุด"], [], ["คลังข้อมูลน้ำแห่งชาติ (สสน.)"])
            lines = ["ลิงก์ภาพล่าสุด:"]
            for label, item in links[:5]:
                time = _time_th(item.get("time"))
                lines.append(f"• {label}: {item['image']} · {time or 'เวลาไม่ระบุ'}")
            return finish(lines, [item for _, item in links[:5]], [media.get("source") or "คลังข้อมูลน้ำแห่งชาติ (สสน.)"])

        return AgentAnswer("ยังไม่มีข้อมูลสำหรับคำถามนี้", intent, None, [])

    def _national(self, question: str, u: Understanding) -> AgentAnswer:
        records = self.tools.get_all_assessments()
        hazards = {h.value for h in u.hazards}
        province_codes = {a.code for a in self.gazetteer.areas if a.level == "province"}
        if u.regions:
            from geo_integration.regions import REGION_CODES
            province_codes &= {code for region in u.regions for code in REGION_CODES[region]}
        rows = [r for r in records if r["hazard"] in hazards and r["area_code"][:4] in province_codes]
        if not rows:
            return AgentAnswer("ยังไม่มีผลประเมินพื้นที่ให้สรุปในขณะนี้", u.intent, None, sorted(hazards), tool_calls=["get_all_assessments()"])
        rank = {"NORMAL": 0, "WATCH": 1, "WARNING": 2, "CRITICAL": 3}
        grouped = {}
        for row in rows:
            grouped.setdefault((row["area_code"][:4], row["hazard"]), []).append(row)
        names = {a.code: a.name_th for a in self.gazetteer.areas if a.level == "province"}
        lines = []
        evidence = []
        if u.intent == "ranking":
            for hazard in sorted(hazards):
                provinces = []
                for (code, h), items in grouped.items():
                    if h == hazard:
                        worst_level = max((rank.get(item.get("level"), -1) for item in items), default=-1)
                        worst = next(item for item in items if rank.get(item.get("level"), -1) == worst_level)
                        worst_count = sum(rank.get(item.get("level"), -1) == worst_level for item in items)
                        provinces.append((worst_level, worst_count, names.get(code, code), code, worst))
                if not provinces:
                    lines.append(f"{HAZARD_TH[hazard]}: ไม่มีผลประเมินรายอำเภอ")
                    continue
                provinces.sort(key=lambda item: (-item[0], -item[1], item[2]))
                lines.append(f"{HAZARD_TH[hazard]} — 5 จังหวัดที่ระดับสูงสุด:")
                lines.extend(f"• จ.{name}: {LEVEL_TH[item.get('level')]} {count} อำเภอ" for _, count, name, _, item in provinces[:5])
                evidence.extend(item for _, _, _, _, item in provinces[:5])
        else:
            for hazard in sorted(hazards):
                hazard_items = [items for (_, h), items in grouped.items() if h == hazard]
                if not hazard_items:
                    lines.append(f"{HAZARD_TH[hazard]}: ไม่มีผลประเมินรายอำเภอ")
                    continue
                counts = {level: 0 for level in ("NORMAL", "WATCH", "WARNING", "CRITICAL", None)}
                for items in hazard_items:
                    for item in items:
                        if item.get("level") in counts:
                            counts[item.get("level")] += 1
                lines.append(f"{HAZARD_TH[hazard]}: " + " · ".join(f"{LEVEL_TH[level]} {count} อำเภอ" for level, count in counts.items()))
        scope = " / ".join(u.regions) if u.regions else "ทั้งประเทศ"
        text = f"ภาพรวม{scope}จากผลประเมินล่าสุด\n" + "\n".join(lines) + "\nระดับภัยมาจากกฎประเมินและข้อมูลที่มีอยู่"
        return AgentAnswer(text, u.intent, None, sorted(hazards), evidence, [], ["get_all_assessments()"])

    def _sources(self) -> AgentAnswer:
        get = getattr(self.tools, "get_sources", None)
        sources = get() if get else {}
        calls = ["get_sources()"] if get else []
        if not sources:
            return AgentAnswer("ยังไม่มีข้อมูลแหล่งที่มาในขณะนี้ กรุณาลองใหม่อีกครั้ง", "sources", None, [], tool_calls=calls)
        lines = ["แหล่งข้อมูลที่ทันภัยติดตามอยู่:"]
        evidence = []
        for source, state in sorted(sources.items()):
            label = SOURCE_TH.get(source, source)
            ok = state.get("ok")
            status = "พร้อมใช้งาน" if ok is True else "ไม่พร้อมใช้งาน" if ok is False else "ยังไม่ทราบสถานะ"
            lines.append(f"• {label}: {status}")
            evidence.append({"source": source, "ok": ok, "checked_at": state.get("checked_at")})
        return AgentAnswer("\n".join(lines), "sources", None, [], evidence=evidence, tool_calls=calls)

    def _shelters(self, u: Understanding) -> AgentAnswer:
        """Shelters straight from DDPM's list. Never rewritten by the LLM, and never a place the list does not name."""
        get, get_source = getattr(self.tools, "get_shelters", None), getattr(self.tools, "shelter_source", None)
        source = get_source() if get_source else None
        calls = ["shelter_source()"]
        if not get or source is None:
            return AgentAnswer(f"ทันภัยยังไม่มีรายชื่อศูนย์พักพิง\n{SHELTER_CHECK}", "shelter", None, [], tool_calls=calls)
        hazards = [h.value for h in u.hazards]
        source_line = f"ที่มา: {source['source']}" + (f" · ปรับปรุงล่าสุด {_date_th(source['updated'])}" if source.get("updated") else "")
        if not u.area:
            calls.append("get_shelters()")
            total = len(get())
            text = (f"รายชื่อศูนย์พักพิงชั่วคราวของ ปภ. มี {total:,} แห่งทั่วประเทศ (ยังไม่มีกรุงเทพฯ) "
                    "บอกชื่ออำเภอหรือจังหวัดมาได้เลย เช่น \"ศูนย์พักพิงอำเภอเสนา\" "
                    "หรือกดปุ่ม \"หาศูนย์ใกล้ฉัน\" ในชั้นศูนย์พักพิงบนแผนที่\n"
                    f"{SHELTER_ROUTE_HINT}\n{SHELTER_CHECK}\n{source_line}")
            return AgentAnswer(text, "shelter", None, hazards, sources=[source["source"]], tool_calls=calls,
                               notes=[*u.notes, "area required"])
        area = u.area
        label = _area_label(area, self.gazetteer)
        area_info = {"code": area.code, "level": area.level, "label": label, "lat": area.lat, "lon": area.lon}
        if area.code.startswith("TH10"):
            text = ("รายชื่อศูนย์พักพิงของ ปภ. ยังไม่มีกรุงเทพฯ กรุงเทพมหานครประกาศศูนย์พักพิงของแต่ละเขตเอง "
                    "ติดตามได้จากสำนักงานเขต หรือโทรสายด่วน กทม. 1555\n"
                    f"{SHELTER_CHECK}")
            return AgentAnswer(text, "shelter", area_info, hazards, tool_calls=calls)
        calls.append(f"get_shelters({area.code!r})")
        shelters = get(area.code)
        # A sub-district gets its district's list (the same as its risk level); say which district that is.
        scope = area
        if area.level == "tambon" and area.parent_code in self.gazetteer.by_code:
            scope = self.gazetteer.by_code[area.parent_code]
        scope_label = _area_label(scope, self.gazetteer)
        if not shelters:
            text = f"รายชื่อของ ปภ. ไม่มีศูนย์พักพิงใน{scope_label}\n{SHELTER_CHECK}\n{source_line}"
            return AgentAnswer(text, "shelter", area_info, hazards, sources=[source["source"]], tool_calls=calls)
        capacity = sum(s.get("capacity") or 0 for s in shelters)
        lines = [f"ศูนย์พักพิงชั่วคราวใน{scope_label} {len(shelters):,} แห่ง"
                 + (f" รองรับรวม {capacity:,} คน" if capacity else "")]
        if area.level == "tambon":
            lines[0] = f"{label} อยู่ใน{scope_label} " + lines[0]
        if scope.level == "province":
            by_district = Counter(s["amphoe_code"] for s in shelters)
            names = [f"{self.gazetteer.by_code[c].name_th} {n}" for c, n in by_district.most_common(5) if c in self.gazetteer.by_code]
            lines.append("อำเภอที่มีศูนย์มากที่สุด: " + " · ".join(names))
            lines.append("ศูนย์ที่รองรับคนได้มากที่สุด:")
        biggest = sorted(shelters, key=lambda s: -(s.get("capacity") or 0))[:SHELTER_LIST]
        for s in biggest:
            where = " ".join(p for p in (s.get("tambon") and f"ต.{s['tambon']}",
                                         scope.level == "province" and s.get("amphoe") and f"อ.{s['amphoe']}") if p)
            lines.append(f"• {s['name']}" + (f" ({where})" if where else "")
                         + (f" รองรับ {s['capacity']:,} คน" if s.get("capacity") else "")
                         + (" · ตำแหน่งโดยประมาณ" if s.get("approximate") else ""))
        if len(shelters) > len(biggest):
            lines.append(f"และอีก {len(shelters) - len(biggest):,} แห่ง ดูครบทุกแห่งได้บนแผนที่ (เปิดชั้น \"ศูนย์พักพิง\")")
        lines += [SHELTER_ROUTE_HINT, SHELTER_CHECK, source_line]
        return AgentAnswer("\n".join(lines), "shelter", area_info, hazards, biggest, [source["source"]], calls)

    def _dams(self, question: str, u: Understanding) -> AgentAnswer:
        """Dam storage, straight from the data (no LLM rewrite, so every number is exactly as reported)."""
        get = getattr(self.tools, "get_dams", None)
        dams = get() if get else []
        if not dams:
            return AgentAnswer("ยังไม่มีข้อมูลเขื่อนในขณะนี้ กรุณาลองใหม่อีกครั้ง", "dam", None, [], tool_calls=["get_dams()"])
        lowered = question.lower()
        # Dam names in the data have no "เขื่อน" prefix ("ภูมิพล"); match the longest names first.
        named = [d for d in sorted(dams, key=lambda d: -len(d.get("name_th") or ""))
                 if (d.get("name_th") and d["name_th"] in question) or (d.get("name_en") and d["name_en"].lower() in lowered)]
        area_info = None
        if not named and "เจ้าพระยา" in question:
            return self._rivers(question, u, chao_phraya_dam=True)
        if named:
            chosen, title = named[:3], None
            first = chosen[0]
            if first.get("province_code") and first.get("lat"):
                area_info = {"code": first["province_code"], "level": "province", "label": f"จ.{first.get('province_th')}",
                             "lat": first["lat"], "lon": first["lon"]}
        elif u.area:
            province = u.area.code[:4]
            chosen = [d for d in dams if d.get("province_code") == province]
            label = _area_label(self.gazetteer.by_code[province], self.gazetteer) if province in self.gazetteer.by_code else u.area.name_th
            if not chosen:
                return AgentAnswer(f"ไม่มีเขื่อนขนาดใหญ่ใน{label} ข้อมูลครอบคลุมเขื่อนขนาดใหญ่ 35 แห่งของประเทศ",
                                   "dam", None, [], tool_calls=["get_dams()"])
            title = f"เขื่อนขนาดใหญ่ใน{label}:"
        elif "ระบาย" in question or "release" in lowered:
            chosen = sorted((d for d in dams if d.get("released_mcm") is not None), key=lambda d: -d["released_mcm"])[:5]
            total = sum(d["released_mcm"] for d in dams if d.get("released_mcm") is not None)
            title = f"เขื่อนที่ระบายน้ำมากที่สุดวันนี้ (รวมทุกเขื่อนขนาดใหญ่ระบาย {_flow(total)}):"
        else:
            high = [d for d in dams if d.get("status") == "HIGH"]
            low = [d for d in dams if d.get("status") == "LOW_CRITICAL"]
            title = (f"ตอนนี้เขื่อนขนาดใหญ่ {len(dams)} แห่ง มีน้ำใกล้เต็มหรือเกินระดับกักเก็บปกติ {len(high)} แห่ง"
                     + (f" และมีน้ำที่ใช้การได้เหลือน้อย {len(low)} แห่ง" if low else " ยังไม่มีแห่งไหนที่น้ำใช้การได้เหลือน้อย"))
            chosen = (high[:5] + low[:3]) or dams[:5]
        lines = [title] if title else []
        for d in chosen:
            lines.append(_dam_line(d))
        date_text = next((d["date"] for d in chosen if d.get("date")), None)
        if date_text:
            day = date.fromisoformat(date_text)
            lines.append(f"ข้อมูลรายวันของวันที่ {day.day}/{day.month}/{day.year + 543} · % เทียบกับระดับกักเก็บปกติของแต่ละเขื่อน")
        agencies = sorted({d["agency_th"].strip() for d in chosen if d.get("agency_th")})
        # The agency in the data is who reports the daily figures (often RID), not necessarily the dam owner.
        sources = ["ThaiWater (สสน.)"] + [f"รายงานโดย {a}" for a in agencies]
        lines.append("ที่มา: " + ", ".join(sources))
        return AgentAnswer("\n".join(lines), "dam", area_info, [], chosen, sources, ["get_dams()"])

    def _names_a_dam(self, question: str, u: Understanding) -> bool:
        """A large dam's name with no hazard asked about, and not also the name of the place found
        ("บางพระ" is a dam and a tambon: a place question about the tambon stays one)."""
        if detect_hazards(question):
            return False
        get = getattr(self.tools, "get_dams", None)
        places = {a.name_th for a in u.areas}
        return any(len(name) >= 4 and name in question and name not in places
                   for name in ((d.get("name_th") or "") for d in (get() if get else [])))

    def _rivers(self, question: str, u: Understanding, chao_phraya_dam: bool = False) -> AgentAnswer:
        """River flow and bank levels per station, straight from the hourly data (no LLM rewrite)."""
        get = getattr(self.tools, "get_rivers", None)
        rivers = get() if get else []
        if not rivers:
            return AgentAnswer("ยังไม่มีข้อมูลระดับน้ำในแม่น้ำในขณะนี้ กรุณาลองใหม่อีกครั้ง", "river", None, [], tool_calls=["get_rivers()"])
        by_code = {r["code"]: r for r in rivers}
        # Whole codes only: "C.35" must not also match "C.3".
        codes = list(dict.fromkeys(c for c in STATION_CODE.findall(question) if c in by_code))
        named = [r for r in sorted(rivers, key=lambda r: -len(r.get("name_th") or ""))
                 if r.get("name_th") and len(r["name_th"]) >= 4 and r["name_th"] in question]
        # Rivers named in full as the data writes them ("แม่น้ำมูล", "ลำน้ำยัง"). "เจ้าพระยา" alone keeps the
        # main-stem overview below (its tributaries included) unless a place narrows it.
        asked_rivers = [n for n in sorted({r["river_th"] for r in rivers if len(r.get("river_th") or "") >= 4}, key=len, reverse=True)
                        if n in question and (u.area or "เจ้าพระยา" not in n)]
        title = None
        if chao_phraya_dam and CHAO_PHRAYA_DAM_STATION in by_code:
            chosen = [by_code[CHAO_PHRAYA_DAM_STATION]]
            title = ("เขื่อนเจ้าพระยา (ชัยนาท) เป็นเขื่อนทดน้ำ ไม่มีอ่างเก็บน้ำ จึงไม่อยู่ในรายชื่อเขื่อนขนาดใหญ่ 35 แห่ง "
                     "น้ำที่เขื่อนปล่อยลงมาวัดได้ที่สถานีท้ายเขื่อน (C.13):")
        elif codes or named:
            chosen = list({id(r): r for r in [by_code[c] for c in codes] + named}.values())[:3]
        elif asked_rivers:
            on_river = [r for r in rivers if r.get("river_th") in asked_rivers]
            river_label = asked_rivers[0]
            where = ""
            if u.area:
                province = u.area.code[:4]
                label = _area_label(self.gazetteer.by_code[province], self.gazetteer) if province in self.gazetteer.by_code else u.area.name_th
                here = [r for r in on_river if r.get("province_code") == province]
                if here:
                    on_river, where = here, f"ใน{label}"
                else:
                    where = f" (ไม่มีสถานีวัดน้ำบน{river_label}ใน{label} จึงแสดงทุกสถานีบนแม่น้ำสายนี้)"
            # Closest to the bank first, so a question about overflowing sees the answer at the top.
            on_river.sort(key=lambda r: -(r["above_bank_m"] if r.get("above_bank_m") is not None else -999))
            chosen = on_river[:8]
            order = (f"แสดง {len(chosen)} แห่งที่น้ำใกล้ตลิ่งที่สุด" if len(on_river) > len(chosen)
                     else "เรียงจากน้ำใกล้ตลิ่งที่สุด")
            title = f"{river_label}{where}: สถานีวัดน้ำ {len(on_river)} แห่ง {order}:"
        elif u.area:
            province = u.area.code[:4]
            chosen = [r for r in rivers if r.get("province_code") == province][:6]
            label = _area_label(self.gazetteer.by_code[province], self.gazetteer) if province in self.gazetteer.by_code else u.area.name_th
            if not chosen:
                return AgentAnswer(f"ยังไม่มีสถานีวัดน้ำที่วัดอัตราการไหลได้ใน{label}", "river", None, [], tool_calls=["get_rivers()"])
            title = f"สถานีวัดน้ำใน{label}:"
        elif "ล้นตลิ่ง" in question and "เจ้าพระยา" not in question:
            over = [r for r in rivers if r.get("status") == "OVERFLOW"]
            if not over:
                return AgentAnswer("ตอนนี้ไม่มีสถานีวัดน้ำที่น้ำล้นตลิ่ง", "river", None, [], tool_calls=["get_rivers()"])
            chosen = sorted(over, key=lambda r: -(r.get("above_bank_m") or 0))[:8]
            title = f"ตอนนี้มีสถานีวัดน้ำที่น้ำล้นตลิ่ง {len(over)} แห่ง (เรียงจากล้นมากที่สุด):"
        else:
            chosen = sorted((r for r in rivers if r.get("chain_order") is not None), key=lambda r: r["chain_order"])
            title = "แม่น้ำเจ้าพระยาและสาขา จากเหนือลงใต้:"
        lines = [title] if title else []
        lines += [_river_line(r) for r in chosen]
        times = sorted(r["time"] for r in chosen if r.get("time"))
        if times:
            t = datetime.fromisoformat(times[-1]).astimezone(BANGKOK)
            lines.append(f"ข้อมูลรายชั่วโมง ณ {t:%H:%M} น. วันที่ {t.day}/{t.month}/{t.year + 543} · ความจุลำน้ำ = ปริมาณน้ำที่ไหลได้โดยไม่ล้นตลิ่ง")
        agencies = sorted({AGENCY_TH.get(r.get("agency"), r.get("agency")) for r in chosen if r.get("agency")})
        sources = ["ThaiWater (สสน.)"] + [f"สถานีของ{a}" for a in agencies]
        lines.append("ที่มา: " + ", ".join(sources))
        first = chosen[0] if chosen else None
        area_info = ({"code": first["province_code"], "level": "province", "label": f"จ.{first.get('province_th')}",
                      "lat": first["lat"], "lon": first["lon"]} if first and len(chosen) == 1 and first.get("province_code") and first.get("lat") else None)
        return AgentAnswer("\n".join(lines), "river", area_info, [], chosen, sources, ["get_rivers()"])

    def _forecast_days(self, area_code: str, u: Understanding) -> list[dict]:
        """Forecast days inside the asked time window. Empty when the tools have no forecast."""
        get = getattr(self.tools, "get_forecast", None)
        if get is None:
            return []
        start, end = u.window_start.date(), u.window_end.date()
        days = [d for d in get(area_code) if start <= date.fromisoformat(d["date"]) < max(end, start + timedelta(days=1))]
        return [d for d in days if d.get("rain_mm") is not None and d.get("level")]

    def _assess(self, u: Understanding, question: str = "") -> AgentAnswer:
        area = u.area
        calls = [f"get_assessments({area.code})", "get_official_warnings()"]
        assessments = [a for a in self.tools.get_assessments(area.code) if a["hazard"] in {h.value for h in u.hazards}]
        warnings = self.tools.get_official_warnings()
        label = _area_label(area, self.gazetteer)
        lines = [f"{label} — สถานการณ์ล่าสุดจากข้อมูลตรวจวัด"]
        forecast_days = self._forecast_days(area.code, u) if u.future else []
        if forecast_days:
            calls.append(f"get_forecast({area.code})")
            lines.append("พยากรณ์ฝนรายวันจากกรมอุตุนิยมวิทยา (แบบจำลอง WRF):")
            for day in forecast_days:
                d = date.fromisoformat(day["date"])
                extra = f" ({day['cond_th']})" if day.get("cond_th") else ""
                lines.append(f"• พยากรณ์ฝน {d.day}/{d.month}: {LEVEL_TH[day['level']]} — {day['rain_mm']:.1f} มม.{extra}")
        if u.future:
            # Rain has a forecast once TMD NWP is connected; flood, drought and fire still have none.
            without = [HAZARD_TH[h.value] for h in u.hazards if not (h == HazardType.RAIN_STORM and forecast_days)]
            if without:
                lines.append(f"หมายเหตุ: ทันภัยยังไม่มีพยากรณ์ล่วงหน้าของ{'/'.join(without)}สำหรับ{_window_th(u)} "
                             "ข้อมูลด้านล่างคือสถานการณ์ ณ ตอนนี้ ใช้ประกอบการติดตามเท่านั้น")
        for item in sorted(assessments, key=lambda a: list(HazardType).index(HazardType(a["hazard"]))):
            reason = _reason(item)
            lines.append(f"• {HAZARD_TH[item['hazard']]}: {LEVEL_TH[item['level']]}" + (f" — {reason}" if reason else ""))
        missing = {h.value for h in u.hazards} - {a["hazard"] for a in assessments}
        for hazard in sorted(missing):
            lines.append(f"• {HAZARD_TH[hazard]}: ยังไม่มีแบบจำลองหรือข้อมูลสำหรับพื้นที่นี้")
        related = [w for w in warnings if _warning_applies(w, u)]
        for w in related[:2]:
            lines.append(f"⚠ ประกาศกรมอุตุนิยมวิทยา: {w['title'].strip()}")
        if u.future and related:
            lines.append("ประกาศของกรมอุตุนิยมวิทยาครอบคลุมช่วงเวลาข้างหน้า ให้ยึดประกาศทางการเป็นหลัก")
        if u.intent == "explain":
            lines.append("ระดับเตือนภัยตัดสินด้วยกฎที่กำหนดไว้ล่วงหน้าจากข้อมูลตรวจวัดและประกาศทางการ ไม่ได้ให้ AI ตัดสินเอง")
        if any(a["level"] in ("WARNING", "CRITICAL") for a in assessments):
            lines.append(EMERGENCY_TH)
        sources = sorted({s for a in assessments for s in a.get("sources", [])}
                         | ({"กรมอุตุนิยมวิทยา (TMD)"} if related or forecast_days else set()))
        if sources:
            lines.append("ที่มา: " + ", ".join(sources))
        area_info = {"code": area.code, "level": area.level, "label": label, "lat": area.lat, "lon": area.lon}
        answer = AgentAnswer("\n".join(lines), u.intent, area_info,
                             [h.value for h in u.hazards], assessments + related, sources, calls, u.notes)
        if u.intent == "prepare":
            return self._prepare(question, u, answer.evidence, prefix=answer.text)
        return answer

    def _prepare(self, question: str, u: Understanding, evidence: list[dict], prefix: str = "") -> AgentAnswer:
        lines = [prefix] if prefix else []
        shown: set[str] = set()
        for hazard in u.hazards:
            lines.append(f"การเตรียมพร้อมรับ{HAZARD_TH[hazard.value]}:")
            steps = []
            official, team = [], []
            source_citation = None
            if get_disaster_guidance is not None:
                actual_level = None
                for ev in evidence:
                    if ev.get("hazard") == hazard.value:
                        actual_level = ev.get("level") or ev.get("alert_level")
                        break
                passages = get_disaster_guidance(hazard=hazard.value, alert_level=actual_level, query=question, top_k=2)
                official = [p for p in passages if p.citation and p.id not in shown]
                team = [p for p in passages if not p.citation and p.id not in shown]
                shown.update(p.id for p in official + team)
                if actual_level:
                    official.sort(key=lambda p: (
                        0 if p.alert_levels == [actual_level.upper()] else (
                            1 if actual_level.upper() in [l.upper() for l in p.alert_levels] else 2
                        )
                    ))
                steps = list(dict.fromkeys(s for p in official for s in p.procedure_steps))
                by_doc: dict[str, list[int]] = {}
                for p in official:
                    by_doc.setdefault(f"{p.authority} {p.title}", []).extend(p.pages)
                source_citation = " · ".join(f"[{doc} หน้า {format_page_ranges(pages)}]" for doc, pages in by_doc.items()) if by_doc else None

            team_steps = list(dict.fromkeys(s for p in team for s in p.procedure_steps))
            if not any(p.hazard == hazard.value for p in official):
                # ยังไม่มี passage ของภัยนี้: PREPARE_TH เป็นคำแนะนำของทีมเสมอ
                team_steps = list(dict.fromkeys([*PREPARE_TH.get(hazard.value, []), *team_steps]))

            if steps:
                lines.extend(f"  - {step}" for step in steps)
                if source_citation:
                    lines.append(f"  แหล่งอ้างอิง: {source_citation}")
            if team_steps:
                lines.append("  คำแนะนำเพิ่มเติม (ไม่ได้มาจากคู่มือราชการ):")
                lines.extend(f"  - {step}" for step in team_steps)
        return AgentAnswer("\n".join(lines), "prepare", None, [h.value for h in u.hazards], evidence, notes=u.notes)
