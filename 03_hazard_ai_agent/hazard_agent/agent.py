"""Hazard agent: understand the question, collect evidence through tools, and answer with sources.

The deterministic path below works without any LLM. An LLM can later replace `understand` for hard
questions and `compose` for wording, but it must keep the same evidence and alert levels.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Callable, Protocol

from geo_integration import Area, Gazetteer
from hazard_agent.understanding import BANGKOK, STATION_CODE, Understanding, understand
from thanphai_shared import HazardType

LEVEL_TH = {"NORMAL": "ปกติ", "WATCH": "เฝ้าระวัง", "WARNING": "เตือนภัย", "CRITICAL": "วิกฤต", None: "ไม่มีข้อมูลเพียงพอ"}
HAZARD_TH = {"flood": "น้ำท่วม", "rain_storm": "ฝนหนัก/พายุ", "drought": "ภัยแล้ง", "fire": "ไฟป่า/ไฟหญ้า"}
# Lines of the rule-based answer that are always shown as written, even when the LLM rewrites the rest.
VERBATIM_PREFIXES = ("⚠", "ประกาศของกรมอุตุนิยมวิทยาครอบคลุม", "ระดับเตือนภัยตัดสินด้วยกฎ", "หากอยู่ในอันตราย", "ที่มา:")
EMERGENCY_TH = "หากอยู่ในอันตราย โทร 1784 (ปภ.) · 1669 (เจ็บป่วยฉุกเฉิน) · 199 (ดับเพลิง) ทันที"
PREPARE_TH = {
    "flood": ["ยกของมีค่าและปลั๊กไฟขึ้นที่สูง", "เตรียมยา น้ำดื่ม อาหารแห้ง ไฟฉาย และเอกสารสำคัญใส่ถุงกันน้ำ", "ติดตามประกาศของ ปภ. และเตรียมเส้นทางอพยพ"],
    "rain_storm": ["หลีกเลี่ยงการเดินทางช่วงฝนตกหนัก", "อยู่ห่างจากต้นไม้ใหญ่และป้ายโฆษณา", "ระวังน้ำป่าไหลหลากหากอยู่ใกล้ภูเขาหรือลำห้วย"],
    "drought": ["กักเก็บน้ำสำรองและใช้น้ำอย่างประหยัด", "ติดตามประกาศพื้นที่ประสบภัยแล้งจาก ปภ.", "วางแผนการเพาะปลูกตามปริมาณน้ำที่มี"],
    "fire": ["งดเผาในที่โล่ง", "ปิดประตูหน้าต่างเมื่อมีควัน และใช้หน้ากาก N95", "แจ้ง 1362 (กรมอุทยานฯ) เมื่อพบไฟป่า"],
}


class HazardTools(Protocol):
    """Tools the agent may call. Implemented by the API backend (module 02)."""

    def get_assessments(self, area_code: str) -> list[dict]: ...
    def get_official_warnings(self) -> list[dict]: ...


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
    if obs.get("hotspots_24h") is not None:
        parts.append(f"จุดความร้อน 24 ชม. {int(obs['hotspots_24h'])} จุด")
    if obs.get("fire_probability_7d") is not None:
        parts.append(f"แบบจำลองประเมินโอกาสเกิดไฟใน 7 วัน {obs['fire_probability_7d'] * 100:.0f}%")
    if obs.get("vci") is not None:
        parts.append(f"ดัชนีความสมบูรณ์ของพืช (VCI) {obs['vci']:.0f} จาก 100 เทียบเดือนเดียวกันในอดีต")
    elif obs.get("ndvi_deficit") is not None:
        parts.append("พืชพรรณเขียวใกล้เคียงปกติของเดือนนี้" if obs["ndvi_deficit"] < 0.05 else "พืชพรรณเขียวน้อยกว่าปกติ")
    if obs.get("spi_3") is not None:
        parts.append(f"ดัชนี SPI-3 {obs['spi_3']:+.2f}")
    if "OFFICIAL_WARNING" in (item.get("rule_ids") or []):
        parts.append("มีประกาศเตือนจากกรมอุตุนิยมวิทยา")
    return ", ".join(parts)


class HazardAgent:
    def __init__(self, gazetteer: Gazetteer, tools: HazardTools, explainer: Explainer | None = None):
        self.gazetteer = gazetteer
        self.tools = tools
        self.explainer = explainer

    def ask(self, question: str, now: datetime) -> AgentAnswer:
        # "พรุ่งนี้" means tomorrow in Thailand; with a UTC clock it would be wrong between 00:00 and 07:00.
        u = understand(question, self.gazetteer, now.astimezone(BANGKOK))
        if u.intent == "emergency":
            return AgentAnswer(EMERGENCY_TH, u.intent, None, [h.value for h in u.hazards])
        if u.intent == "dam":
            return self._dams(question, u)
        if u.intent == "river":
            return self._rivers(question, u)
        if u.intent == "out_of_scope":
            text = "ทันภัยตอบได้เฉพาะเรื่องฝนหนัก/พายุ น้ำท่วม ภัยแล้ง และไฟป่าในประเทศไทยครับ ลองถามเช่น \"น้ำจะท่วมอำเภอบางบัวทองไหม\""
            return AgentAnswer(text, u.intent, None, [])
        if u.intent == "prepare" and not u.area:
            return self._prepare(u, [])
        if not u.area:
            text = "ไม่พบชื่อพื้นที่ในคำถาม ช่วยระบุอำเภอหรือจังหวัด เช่น \"อำเภอเสนา จังหวัดพระนครศรีอยุธยา\" ครับ"
            return AgentAnswer(text, u.intent, None, [h.value for h in u.hazards], notes=u.notes)
        if "ambiguous area" in u.notes and u.area.level == "tambon":
            options = " / ".join(_area_label(a, self.gazetteer) for a in u.areas[:4])
            return AgentAnswer(f"พบหลายพื้นที่ชื่อนี้: {options} หมายถึงที่ไหนครับ", "clarify", None, [h.value for h in u.hazards], notes=u.notes)
        answer = self._assess(u)
        if self.explainer and answer.intent in ("risk", "explain"):
            answer.facts = answer.text
            rewritten = self.explainer(question, answer.text)
            if rewritten:
                # The LLM writes only the summary. Official warnings, emergency numbers and sources are copied
                # from the rule-based answer word for word, so a rewrite can never drop or change them.
                tail = [line for line in answer.text.splitlines() if line.startswith(VERBATIM_PREFIXES)]
                answer.text, answer.llm_used = "\n".join([rewritten.strip(), *tail]), True
        return answer

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
        title = None
        if chao_phraya_dam and CHAO_PHRAYA_DAM_STATION in by_code:
            chosen = [by_code[CHAO_PHRAYA_DAM_STATION]]
            title = ("เขื่อนเจ้าพระยา (ชัยนาท) เป็นเขื่อนทดน้ำ ไม่มีอ่างเก็บน้ำ จึงไม่อยู่ในรายชื่อเขื่อนขนาดใหญ่ 35 แห่ง "
                     "น้ำที่เขื่อนปล่อยลงมาวัดได้ที่สถานีท้ายเขื่อน (C.13):")
        elif codes or named:
            chosen = list({id(r): r for r in [by_code[c] for c in codes] + named}.values())[:3]
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

    def _assess(self, u: Understanding) -> AgentAnswer:
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
        related = [w for w in warnings if set(w.get("hazards", [])) & {h.value for h in u.hazards}]
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
            return self._prepare(u, answer.evidence, prefix=answer.text)
        return answer

    def _prepare(self, u: Understanding, evidence: list[dict], prefix: str = "") -> AgentAnswer:
        lines = [prefix] if prefix else []
        for hazard in u.hazards:
            lines.append(f"การเตรียมพร้อมรับ{HAZARD_TH[hazard.value]}:")
            steps = None
            source_citation = None
            try:
                from decision_engine.rag import get_disaster_guidance
                passages = get_disaster_guidance(hazard.value, top_k=1)
                if passages:
                    steps = passages[0].procedure_steps
                    source_citation = passages[0].citation
            except Exception:
                pass
            if not steps:
                steps = PREPARE_TH[hazard.value]
            lines.extend(f"  - {step}" for step in steps)
            if source_citation:
                lines.append(f"  แหล่งอ้างอิง: {source_citation}")
        if not any("แหล่งอ้างอิง:" in line for line in lines):
            lines.append("แหล่งข้อมูลแนะนำ: คู่มือเตรียมพร้อมรับมือสาธารณภัย ปภ. (จะเชื่อมกับ RAG ในโมดูล 07)")
        return AgentAnswer("\n".join(lines), "prepare", None, [h.value for h in u.hazards], evidence, notes=u.notes)
