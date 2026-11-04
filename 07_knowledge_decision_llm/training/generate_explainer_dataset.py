"""Generate the instruction-tuning dataset for the explainer (Qwen LoRA).

The facts come from the real HazardAgent: each sample gives the agent made-up assessments for a random district,
asks it a question, and keeps the exact facts the agent would hand to the LLM. So the training facts look like
production: one hazard when one hazard is asked, forecast days and the no-forecast note for future questions,
"no data" lines, and the verbatim lines (official warnings, emergency numbers, sources) the LLM must leave out.

Questions vary (overview, one or two hazards, "is it safe", tomorrow / next days, why this level) and about a
quarter are in English, answered in English as SYSTEM_INSTRUCTION rule 6 requires. Answers lead with what was
asked, keep each hazard's level and measured values, and have at most 4 lines. Every answer passes validate().

Test districts are held out from training, so the test set checks answers for places the model has not seen.

Saves:
  train_explainer.jsonl
  test_explainer.jsonl
"""

import argparse
import json
import random
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from decision_engine.explainer import SYSTEM_INSTRUCTION, validate
from geo_integration import Gazetteer
from geo_integration.gazetteer import Area
from hazard_agent import HazardAgent
from hazard_agent.agent import HAZARD_TH

HAZARDS = list(HAZARD_TH)
LEVELS_TH = ("ปกติ", "เฝ้าระวัง", "เตือนภัย", "วิกฤต")
LEVEL_KEY = {"NORMAL": "ปกติ", "WATCH": "เฝ้าระวัง", "WARNING": "เตือนภัย", "CRITICAL": "วิกฤต"}
LEVEL_EN = {"ปกติ": "NORMAL", "เฝ้าระวัง": "WATCH", "เตือนภัย": "WARNING", "วิกฤต": "CRITICAL"}
RANK = {lvl: i for i, lvl in enumerate(LEVELS_TH)}
TH_TO_HAZARD = {v: k for k, v in HAZARD_TH.items()}
# The hazards whose rules in rules.yaml reach CRITICAL.
HAS_CRITICAL = {"flood", "drought", "heat", "earthquake", "marine", "tropical_cyclone", "landslide"}
HAZARD_EN = {
    "flood": "flood", "rain_storm": "heavy rain/storm", "fire": "forest fire", "drought": "drought",
    "air_quality": "PM2.5", "heat": "heat", "earthquake": "earthquake", "marine": "strong waves",
    "tropical_cyclone": "tropical cyclone", "landslide": "landslide",
}
# Names for a grouped "A, B and C are NORMAL": validate() carries a level back over at most 8 characters between
# two hazard names, so a list may not use names whose keyword sits behind a prefix ("forest fire", "ไฟป่า/ไฟหญ้า").
GROUP_TH = {**HAZARD_TH, "fire": "ไฟป่า"}
GROUP_EN = {**HAZARD_EN, "fire": "fire", "marine": "waves", "tropical_cyclone": "cyclone"}
THAI = re.compile(r"[฀-๿]")
INPUT_TEMPLATE = "คำถามผู้ใช้: {question}\n\nข้อเท็จจริง (ห้ามเปลี่ยน):\n{facts}"
MAX_LINES = 4

# --- Questions -----------------------------------------------------------------------------------------------

ASK_ONE_TH = {
    "flood": ["{a}น้ำท่วมไหม", "น้ำที่{a}ล้นตลิ่งหรือยัง", "ระดับน้ำที่{a}เป็นยังไง", "{a}มีน้ำท่วมหรือเปล่า"],
    "rain_storm": ["ฝนที่{a}หนักแค่ไหน", "{a}ฝนตกหนักไหม", "ฝนที่{a}เป็นยังไงบ้าง"],
    "fire": ["{a}มีไฟป่าไหม", "จุดความร้อนที่{a}เยอะไหม", "ไฟป่าที่{a}เป็นยังไง"],
    "drought": ["{a}แล้งไหม", "ภัยแล้งที่{a}เป็นยังไง"],
    "air_quality": ["ฝุ่นที่{a}เป็นไง", "ค่า PM2.5 ที่{a}เท่าไหร่", "{a}ฝุ่นเยอะไหม"],
    "heat": ["{a}ร้อนจัดไหม", "ดัชนีความร้อนที่{a}เท่าไหร่"],
    "earthquake": ["{a}มีแผ่นดินไหวไหม", "แผ่นดินไหวที่{a}เป็นยังไง"],
    "marine": ["คลื่นที่{a}แรงไหม", "ทะเลที่{a}คลื่นสูงไหม"],
    "tropical_cyclone": ["{a}มีพายุหมุนเขตร้อนไหม", "พายุหมุนเขตร้อนจะเข้า{a}ไหม"],
    "landslide": ["{a}เสี่ยงดินถล่มไหม", "ดินถล่มที่{a}เป็นยังไง"],
}
ASK_ONE_EN = {
    "flood": ["Is {A} flooded?", "Is there flooding in {A}?", "What is the water level in {A}?"],
    "rain_storm": ["How heavy is the rain in {A}?", "Is there heavy rain in {A}?"],
    "fire": ["Are there forest fires in {A}?", "Any fire hotspots in {A}?"],
    "drought": ["Is there drought in {A}?"],
    "air_quality": ["How is the PM2.5 in {A}?", "What is the PM2.5 level in {A}?"],
    "heat": ["Is it dangerously hot in {A}?", "What is the heat index in {A}?"],
    "earthquake": ["Was there an earthquake near {A}?"],
    "marine": ["Are the waves high in {A}?"],
    "tropical_cyclone": ["Is a tropical cyclone coming to {A}?"],
    "landslide": ["Is {A} at risk of landslides?"],
}
SHORT_TH = {"flood": "น้ำท่วม", "rain_storm": "ฝนหนัก", "fire": "ไฟป่า", "air_quality": "ฝุ่น", "heat": "ร้อนจัด", "drought": "แล้ง"}
SHORT_EN = {"flood": "flooding", "rain_storm": "heavy rain", "fire": "forest fire", "air_quality": "PM2.5", "heat": "extreme heat", "drought": "drought"}
ASK_OVERVIEW_TH = ["สถานการณ์ที่{a}เป็นอย่างไรบ้าง", "{a}ตอนนี้เป็นยังไงบ้าง", "{a}มีภัยอะไรบ้าง", "ขอสรุปสถานการณ์ภัยที่{a}", "เช็กภัยที่{a}ให้หน่อย"]
ASK_OVERVIEW_EN = ["What is the situation in {A}?", "Any hazards in {A} right now?", "Give me a hazard summary for {A}."]
ASK_SAFE_TH = ["{a}ปลอดภัยไหม", "{a}น่าห่วงไหม", "ตอนนี้อยู่{a}ปลอดภัยหรือเปล่า"]
ASK_SAFE_EN = ["Is {A} safe right now?", "Is it safe in {A}?"]
ASK_FUTURE_TH = ["พรุ่งนี้{a}จะน้ำท่วมไหม", "พรุ่งนี้ฝนที่{a}จะตกหนักไหม", "พรุ่งนี้{a}เป็นยังไง", "{n} วันข้างหน้า{a}เป็นยังไง",
                 "พรุ่งนี้{a}ฝุ่นจะเยอะไหม", "{n} วันข้างหน้าฝนที่{a}จะตกหนักไหม"]
ASK_FUTURE_EN = ["Will it flood in {A} tomorrow?", "Will it rain in {A} tomorrow?", "What will {A} be like in the next {n} days?"]
ASK_EXPLAIN_TH = ["ทำไม{a}ถึงเป็นระดับ{lvl}", "ระดับภัยที่{a}มาจากอะไร", "ทำไมระดับภัยที่{a}ถึงเป็นแบบนี้"]
ASK_EXPLAIN_EN = ["Why is {A} at {LEVEL}?", "How were the hazard levels for {A} decided?"]
KIND_WEIGHTS = {"overview": 22, "one": 33, "two": 7, "safe": 10, "future": 20, "explain": 8}
ENGLISH_SHARE = 0.25


# --- Made-up assessments, shaped like the live ones ----------------------------------------------------------

def _observations(hazard: str, level: str, rng: random.Random) -> tuple[dict, list[str]]:
    """Observations that agree with the level, using the keys hazard_agent.agent._reason reads."""
    u = rng.uniform
    if hazard == "flood":
        v = {"NORMAL": -u(0.31, 3.0), "WATCH": -u(0.01, 0.30), "WARNING": u(0.01, 0.49), "CRITICAL": u(0.5, 3.5)}[level]
        obs = {"above_bank_m": round(v, 2)}
        if level != "NORMAL" and rng.random() < 0.3:
            obs["satellite_flood_cells_7d"] = rng.randint(20, 900)
        return obs, []
    if hazard == "rain_storm":
        obs = {"rain_24h": round({"NORMAL": u(0, 35), "WATCH": u(35.1, 90), "WARNING": u(90.1, 200)}[level], 1)}
        rules = []
        if level != "NORMAL" and rng.random() < 0.3:
            rules = [rng.choice(["OFFICIAL_WARNING", "OFFICIAL_WARNING_HEAVY_RAIN", "OFFICIAL_WARNING_VERY_HEAVY_RAIN",
                                 "OFFICIAL_WARNING_REGIONAL", "OFFICIAL_WARNING_UPCOMING"])]
        return obs, rules
    if hazard == "fire":
        lo, hi = {"NORMAL": (0, 2), "WATCH": (3, 9), "WARNING": (10, 40)}[level]
        obs = {"hotspots_24h": rng.randint(lo, hi)}
        if rng.random() < 0.6:
            obs["fire_probability_7d"] = round(u(0.02, 0.9), 2)
        if rng.random() < 0.15:
            obs["cross_border_hotspots_24h"] = rng.randint(1, 60)
        return obs, []
    if hazard == "drought":
        obs = {"spi_3": round({"NORMAL": u(-0.99, 1.5), "WATCH": u(-1.49, -1.0), "WARNING": u(-1.99, -1.5), "CRITICAL": u(-3.0, -2.0)}[level], 2)}
        if rng.random() < 0.5:
            obs["vci"] = rng.randint(40, 90) if level == "NORMAL" else rng.randint(5, 39)
        return obs, []
    if hazard == "air_quality":
        v = round({"NORMAL": u(5, 37.5), "WATCH": u(37.6, 75), "WARNING": u(75.1, 150)}[level], 1)
        obs = {"pm25_24h": v} if rng.random() < 0.8 else {}
        if not obs or rng.random() < 0.4:
            obs["pm25_model_24h"] = round(max(1.0, v + u(-10, 10)), 1)
        return obs, []
    if hazard == "heat":
        return {"heat_index_max_24h": round({"NORMAL": u(27, 39.3), "WATCH": u(39.4, 41.9), "WARNING": u(42, 51.9), "CRITICAL": u(52, 58)}[level], 1)}, []
    if hazard == "earthquake":
        if level == "NORMAL":
            return {"quake_mmi_24h": 0}, []
        mmi = {"WATCH": rng.randint(4, 5), "WARNING": 6, "CRITICAL": rng.randint(7, 8)}[level]
        return {"quake_mmi_24h": mmi, "quake_magnitude": round(u(3.5, 7.8), 1), "quake_distance_km": rng.randint(20, 900)}, []
    if hazard == "marine":
        return {"wave_height_max_24h": round({"NORMAL": u(0.5, 1.9), "WATCH": u(2.0, 2.9), "WARNING": u(3.0, 3.9), "CRITICAL": u(4.0, 6.0)}[level], 1)}, []
    if hazard == "tropical_cyclone":
        if level == "NORMAL":
            return {}, []
        lo, hi = {"WATCH": (63, 92), "WARNING": (93, 118), "CRITICAL": (119, 180)}[level]
        return {"cyclone_wind_kmh": rng.randint(lo, hi), "cyclone_distance_km": rng.randint(30, 600)}, []
    if hazard == "landslide":
        obs = {"landslide_traces": rng.randint(1, 40)}
        if level == "NORMAL":
            obs["landslide_rain_24h"] = round(u(0, 60), 1)
        elif level == "WATCH":
            obs["landslide_rain_24h"] = round(u(10, 90), 1)
            obs["landslide_forecast_rain_24h"] = round(u(100.1, 150), 1)
        else:
            obs["landslide_rain_24h"] = round(u(100.1, 150) if level == "WARNING" else u(150.1, 250), 1)
        return obs, []
    raise ValueError(hazard)


RULE_EN = {
    "OFFICIAL_WARNING": "official TMD warning in effect",
    "OFFICIAL_WARNING_VERY_HEAVY_RAIN": "TMD expects very heavy rain in this province",
    "OFFICIAL_WARNING_HEAVY_RAIN": "TMD expects heavy rain in this province",
    "OFFICIAL_WARNING_REGIONAL": "TMD warning for the whole region (this province not named)",
    "OFFICIAL_WARNING_UPCOMING": "advance TMD warning (impact period not started)",
}


def reason_en(obs: dict, rule_ids: list[str]) -> str:
    """English twin of hazard_agent.agent._reason, with the same numbers in the same format."""
    p = []
    if (v := obs.get("above_bank_m")) is not None:
        p.append(f"water {abs(v):.2f} m {'above' if v > 0 else 'below'} the bank")
    if cells := obs.get("satellite_flood_cells_7d"):
        p.append(f"water seen by satellite over about {cells * 0.1245:.0f} sq km in 7 days")
    if (v := obs.get("rain_24h")) is not None:
        p.append(f"max 24-h rainfall {v:.1f} mm")
    if (v := obs.get("hotspots_24h")) is not None:
        p.append(f"{int(v)} hotspots in 24 h")
    if (v := obs.get("fire_probability_7d")) is not None:
        p.append(f"model chance of fire in 7 days {v * 100:.0f}%")
    if (v := obs.get("pm25_24h")) is not None:
        p.append(f"24-h average PM2.5 {v:.1f} µg/m³ (PCD station)")
    if (v := obs.get("pm25_model_24h")) is not None:
        p.append(f"model expects a 24-h average PM2.5 of {v:.1f} µg/m³ ahead")
    if (v := obs.get("heat_index_max_24h")) is not None:
        p.append(f"max heat index in the next 24 h {v:.1f}°C")
    if obs.get("quake_mmi_24h") is not None:
        if obs.get("quake_magnitude") is not None:
            p.append(f"magnitude {obs['quake_magnitude']:.1f} quake about {obs['quake_distance_km']:.0f} km away, "
                     f"estimated shaking here level {obs['quake_mmi_24h']:.0f} (Mercalli)")
        else:
            p.append("no earthquake in the past 24 h")
    if (v := obs.get("wave_height_max_24h")) is not None:
        p.append(f"max wave height in the next 24 h about {v:.1f} m")
    if v := obs.get("cyclone_wind_kmh"):
        p.append(f"inside the cyclone's strong-wind radius of {v:.0f} km/h or more (including the expected track)")
    if (v := obs.get("cyclone_distance_km")) is not None:
        p.append(f"closest approach about {v:.0f} km")
    if obs.get("landslide_traces") is not None:
        p.append(f"past landslides here ({obs['landslide_traces']:.0f} traces found)")
        if (v := obs.get("landslide_rain_24h")) is not None:
            p.append(f"max 24-h rain {v:.1f} mm")
        if (v := obs.get("landslide_forecast_rain_24h")) is not None:
            p.append(f"max daily rain expected {v:.1f} mm")
    if v := obs.get("cross_border_hotspots_24h"):
        p.append(f"{int(v)} hotspots in neighbouring countries within 100 km (24 h)")
    if (v := obs.get("vci")) is not None:
        p.append(f"vegetation condition index (VCI) {v:.0f} of 100 vs the same month in past years")
    if (v := obs.get("spi_3")) is not None:
        p.append(f"SPI-3 {v:+.2f}")
    for rule in rule_ids:
        if rule in RULE_EN:
            p.append(RULE_EN[rule])
            break
    return ", ".join(p)


@dataclass
class Scenario:
    now: datetime
    assessments: list[dict]
    forecast: list[dict]
    warnings: list[dict]


def make_scenario(area: Area, rng: random.Random) -> Scenario:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=rng.randint(0, 364), hours=rng.randint(0, 12))
    assessments = []
    for hazard in HAZARDS:
        if rng.random() > 0.75:
            continue  # no model for this area: the agent writes "ยังไม่มีแบบจำลองหรือข้อมูล"
        if rng.random() < 0.05:
            assessments.append({"hazard": hazard, "level": None, "observations": {}, "rule_ids": [], "sources": []})
            continue
        levels = ["NORMAL", "WATCH", "WARNING"] + (["CRITICAL"] if hazard in HAS_CRITICAL else [])
        level = rng.choices(levels, weights=[50, 22, 17, 11][: len(levels)])[0]
        obs, rules = _observations(hazard, level, rng)
        assessments.append({"hazard": hazard, "level": level, "observations": obs, "rule_ids": rules, "sources": []})
    forecast = []
    if rng.random() < 0.8:
        for i in range(5):
            mm = round(rng.choice([rng.uniform(0, 35), rng.uniform(0, 35), rng.uniform(35.1, 90), rng.uniform(90.1, 160)]), 1)
            level = "NORMAL" if mm <= 35 else "WATCH" if mm <= 90 else "WARNING"
            cond = rng.choice([None, "ฝนเล็กน้อย", "ฝนปานกลาง", "ฝนหนัก", "ฝนฟ้าคะนอง"])
            forecast.append({"date": (now.date() + timedelta(days=i)).isoformat(), "rain_mm": mm, "level": level, "cond_th": cond})
    warnings = []
    if rng.random() < 0.12:
        hazard = rng.choice(["rain_storm", "marine", "tropical_cyclone", "flood"])
        warnings.append({
            "title": f"ประกาศกรมอุตุนิยมวิทยา เรื่อง {rng.choice(['ฝนตกหนักถึงหนักมาก', 'คลื่นลมแรง', 'พายุโซนร้อน'])} ฉบับที่ {rng.randint(1, 30)}",
            "hazards": [hazard], "provinces": [area.code[:4]], "impact_from": None, "impact_to": None,
        })
    return Scenario(now, assessments, forecast, warnings)


class ScenarioTools:
    """The agent's tools, answering from the current scenario."""

    scenario: Scenario

    def get_assessments(self, area_code: str) -> list[dict]:
        return self.scenario.assessments

    def get_official_warnings(self) -> list[dict]:
        return self.scenario.warnings

    def get_forecast(self, area_code: str) -> list[dict]:
        return self.scenario.forecast


# --- Reading the agent's facts --------------------------------------------------------------------------------

HAZARD_LINE = re.compile(r"^• (.+?): (ปกติ|เฝ้าระวัง|เตือนภัย|วิกฤต|ไม่มีข้อมูลเพียงพอ|ยังไม่มีแบบจำลองหรือข้อมูลสำหรับพื้นที่นี้)(?: — (.*))?$")
FORECAST_FACT = re.compile(r"^• พยากรณ์ฝน (\S+): (ปกติ|เฝ้าระวัง|เตือนภัย|วิกฤต) — ([\d.]+) มม\.")
NOTE_FACT = re.compile(r"^หมายเหตุ: ทันภัยยังไม่มีพยากรณ์ล่วงหน้าของ(.+?)สำหรับ(วันที่ \S+|ช่วง \S+) ")
# The TMD heat band reuses level words ("เตือนภัย") for another scale; repeating it would read as a second level.
HEAT_BAND = re.compile(r" \(เกณฑ์กรมอุตุฯ: [^)]*\)")


@dataclass
class Facts:
    label: str
    forecast: list[tuple[str, str, str]] = field(default_factory=list)  # (day, level, mm)
    note: tuple[str, str] | None = None  # (hazards without forecast, window)
    rated: list[tuple[str, str, str]] = field(default_factory=list)  # (hazard, level, Thai reason)
    no_data: list[str] = field(default_factory=list)
    no_model: list[str] = field(default_factory=list)


def parse_facts(text: str) -> Facts:
    lines = text.splitlines()
    facts = Facts(label=lines[0].split(" — ")[0])
    for line in lines[1:]:
        if m := FORECAST_FACT.match(line):
            facts.forecast.append(m.groups())
        elif m := NOTE_FACT.match(line):
            facts.note = (m.group(1), m.group(2))
        elif (m := HAZARD_LINE.match(line)) and m.group(1) in TH_TO_HAZARD:
            hazard, status, reason = TH_TO_HAZARD[m.group(1)], m.group(2), m.group(3) or ""
            if status in RANK:
                facts.rated.append((hazard, status, HEAT_BAND.sub("", reason)))
            elif status == "ไม่มีข้อมูลเพียงพอ":
                facts.no_data.append(hazard)
            else:
                facts.no_model.append(hazard)
    return facts


# --- Writing the answer ----------------------------------------------------------------------------------------

def _join(names: list[str], last: str) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + last + names[-1]


def _order(facts: Facts, asked: list[str]) -> list[tuple[str, str, str]]:
    """What was asked first, then the most severe."""
    return sorted(facts.rated, key=lambda r: (r[0] not in asked, -RANK[r[1]], HAZARDS.index(r[0])))


def answer_th(facts: Facts, kind: str, asked: list[str], rng: random.Random) -> str:
    out = []
    if facts.forecast:
        if len(facts.forecast) == 1:
            day, lvl, mm = facts.forecast[0]
            out.append(f"พยากรณ์ฝนวันที่ {day} อยู่ในระดับ{lvl} ({mm} มม.)")
        else:
            days = ", ".join(f"{d} {lvl} ({mm} มม.)" for d, lvl, mm in facts.forecast)
            out.append(rng.choice(["พยากรณ์ฝนจากกรมอุตุนิยมวิทยา: {}", "พยากรณ์ฝนรายวัน: {}"]).format(days))
    if facts.note:
        hazards, window = facts.note
        if len(hazards) <= 30:
            line = f"ทันภัยยังไม่มีพยากรณ์ล่วงหน้าของ{hazards}สำหรับ{window}"
        elif facts.forecast and "ฝนหนัก/พายุ" not in hazards:
            line = f"นอกจากฝนแล้ว ทันภัยยังไม่มีพยากรณ์ล่วงหน้าของภัยอื่นสำหรับ{window}"
        else:
            line = f"ทันภัยยังไม่มีพยากรณ์ล่วงหน้าสำหรับ{window}"
        out.append(line + rng.choice([" ข้อมูลที่มีคือสถานการณ์ตอนนี้", " ด้านล่างเป็นสถานการณ์ ณ ตอนนี้", ""]))

    style = rng.randrange(3)  # one sentence shape per answer

    def sentence(hazard: str, lvl: str, reason: str) -> str:
        name = HAZARD_TH[hazard] + (" " if HAZARD_TH[hazard][-1].isascii() else "")
        if not reason:
            return f"{name}อยู่ในระดับ{lvl}"
        return [f"{name}อยู่ในระดับ{lvl} ({reason})", f"{name}ระดับ{lvl} — {reason}", f"{name.strip()}: {lvl} ({reason})"][style]

    rated = _order(facts, asked)
    missing = [HAZARD_TH[h] for h in facts.no_data + facts.no_model]
    focused = len(asked) <= 2 and kind != "explain"
    if focused:
        if rated:
            prefix = rng.choice([f"ตอนนี้ที่{facts.label} ", f"{facts.label}: ", ""])
            out.append(prefix + " ".join(sentence(*r) for r in rated))
        for h in facts.no_data:
            out.append(f"ตอนนี้ยังไม่มีข้อมูลเพียงพอสำหรับ{HAZARD_TH[h]}ที่{facts.label}")
        for h in facts.no_model:
            out.append(f"ทันภัยยังไม่มีแบบจำลองหรือข้อมูล{HAZARD_TH[h]}สำหรับ{facts.label}")
    else:
        severe = [r for r in rated if r[1] != "ปกติ" or kind == "explain"]
        normal = [r for r in rated if r not in severe]
        if kind == "explain":
            lead = f"ระดับของแต่ละภัยที่{facts.label}มาจากข้อมูลตรวจวัดตามเกณฑ์ของระบบ:"
        elif kind == "safe":
            lead = (f"ในภัยที่มีข้อมูล {facts.label} ยังไม่มีภัยใดสูงกว่าระดับปกติ" if rated and not severe
                    else f"{facts.label} มีภัยที่ต้องติดตาม:" if severe else f"ตอนนี้ยังไม่มีข้อมูลภัยของ{facts.label}")
        else:
            lead = rng.choice([f"สถานการณ์ที่{facts.label}ตอนนี้:", f"{facts.label} ตอนนี้"])
        out.append(" ".join([lead, *(sentence(*r) for r in severe)]))
        tail = []
        if normal:
            tail.append(f"{_join([GROUP_TH[h] for h, _, _ in normal], ' และ')} อยู่ในระดับปกติ")
        if missing:
            tail.append(f"ส่วน{_join(missing, ' และ')}ยังไม่มีข้อมูลสำหรับพื้นที่นี้" if len(missing) <= 3
                        else "ส่วนภัยอื่นยังไม่มีข้อมูลสำหรับพื้นที่นี้")
        if tail:
            out.append(" ".join(tail))
    return "\n".join(out)


def answer_en(facts: Facts, kind: str, asked: list[str], label: str, scenario: Scenario, rng: random.Random) -> str:
    out = []
    if facts.forecast:
        days = ", ".join(f"{d} {LEVEL_EN[lvl]} ({mm} mm)" for d, lvl, mm in facts.forecast)
        out.append(f"TMD rain forecast: {days}")
    if facts.note:
        hazards, window = facts.note
        window = window.split(" ", 1)[1]
        names = [HAZARD_EN[TH_TO_HAZARD[n]] for n in HAZARD_TH.values() if n in hazards]
        if len(names) <= 2:
            line = f"No forecast yet for {_join(names, ' and ')} for {window}"
        elif facts.forecast and "ฝนหนัก/พายุ" not in hazards:
            line = f"Apart from rain, there is no forecast yet for {window}"
        else:
            line = f"There is no forecast yet for {window}"
        out.append(line + rng.choice(["; below is the situation now.", ". The data below is for now.", "."]))

    obs_by_hazard = {a["hazard"]: a for a in scenario.assessments}

    def sentence(hazard: str, lvl: str, _reason: str) -> str:
        a = obs_by_hazard[hazard]
        reason = reason_en(a["observations"], a["rule_ids"])
        name = HAZARD_EN[hazard]
        return f"{name[0].upper()}{name[1:]} is {LEVEL_EN[lvl]}" + (f" ({reason})." if reason else ".")

    rated = _order(facts, asked)
    missing = [HAZARD_EN[h] for h in facts.no_data + facts.no_model]
    focused = len(asked) <= 2 and kind != "explain"
    if focused:
        if rated:
            text = " ".join(sentence(*r) for r in rated)
            first = text.split(" ", 1)[0]
            if not first.isupper():  # "In Sena, flood is ..." but "In Sena, PM2.5 is ..."
                text = text[0].lower() + text[1:]
            out.append(f"In {label}, {text}")
        for h in facts.no_data:
            out.append(f"There is not enough data yet for {HAZARD_EN[h]} in {label}.")
        for h in facts.no_model:
            out.append(f"ThanPhai has no model or data for {HAZARD_EN[h]} in {label} yet.")
    else:
        severe = [r for r in rated if r[1] != "ปกติ" or kind == "explain"]
        normal = [r for r in rated if r not in severe]
        if kind == "explain":
            lead = f"Levels in {label} come from measured data and fixed rules:"
        elif kind == "safe":
            lead = (f"Among hazards with data, nothing in {label} is above NORMAL." if rated and not severe
                    else f"{label} has hazards to watch:" if severe else f"There is no hazard data for {label} yet.")
        else:
            lead = rng.choice([f"Current situation in {label}:", f"{label} right now:"])
        out.append(" ".join([lead, *(sentence(*r) for r in severe)]))
        tail = []
        if normal:
            names = [GROUP_EN[h] for h, _, _ in normal]
            group = _join(names, " and ")
            tail.append(f"{group[0].upper()}{group[1:]} {'is' if len(names) == 1 else 'are'} NORMAL.")
        if missing:
            tail.append(f"No data yet for {_join(missing, ' and ')}." if len(missing) <= 3 else "Other hazards have no data here yet.")
        if tail:
            out.append(" ".join(tail))
    text = "\n".join(out)
    return text[0].upper() + text[1:]


# --- Samples ---------------------------------------------------------------------------------------------------

def _question(kind: str, english: bool, area: Area, scenario: Scenario, rng: random.Random) -> str:
    a, A = area.name_th, area.name_en
    if kind == "one":
        hazard = rng.choice(HAZARDS)
        return rng.choice((ASK_ONE_EN if english else ASK_ONE_TH)[hazard]).format(a=a, A=A)
    if kind == "two":
        h1, h2 = rng.sample(list(SHORT_TH), 2)
        return f"Is there {SHORT_EN[h1]} or {SHORT_EN[h2]} in {A}?" if english else f"{a}{SHORT_TH[h1]}หรือ{SHORT_TH[h2]}ไหม"
    if kind == "safe":
        return rng.choice(ASK_SAFE_EN if english else ASK_SAFE_TH).format(a=a, A=A)
    if kind == "future":
        return rng.choice(ASK_FUTURE_EN if english else ASK_FUTURE_TH).format(a=a, A=A, n=rng.randint(2, 5))
    if kind == "explain":
        levels = [a_["level"] for a_ in scenario.assessments if a_["level"]] or ["NORMAL"]
        top = max(levels, key=lambda lv: RANK[LEVEL_KEY[lv]])
        return rng.choice(ASK_EXPLAIN_EN if english else ASK_EXPLAIN_TH).format(a=a, A=A, lvl=LEVEL_KEY[top], LEVEL=top)
    return rng.choice(ASK_OVERVIEW_EN if english else ASK_OVERVIEW_TH).format(a=a, A=A)


def _english_label(area: Area, gazetteer: Gazetteer) -> str:
    parents = [p.name_en for p in gazetteer.parents(area) if p.name_en]
    return ", ".join([f"{area.name_en} district" if area.level == "amphoe" else area.name_en, *parents])


def generate_explainer_dataset(gazetteer: Gazetteer, areas: list[Area], target_count: int, seed: int = 42,
                               stats: Counter | None = None) -> list[dict]:
    """Samples for the given areas; `stats` collects per-kind counts and why samples were skipped."""
    rng = random.Random(seed)
    stats = stats if stats is not None else Counter()
    tools = ScenarioTools()
    captured: dict[str, str] = {}

    def capture(question: str, facts: str) -> None:
        captured["facts"] = facts
        return None  # the agent keeps its template answer

    agent = HazardAgent(gazetteer, tools, explainer=capture)
    samples, seen = [], set()
    attempts = 0
    while len(samples) < target_count and attempts < target_count * 10:
        attempts += 1
        area = rng.choice(areas)
        kind = rng.choices(list(KIND_WEIGHTS), weights=list(KIND_WEIGHTS.values()))[0]
        english = bool(area.name_en) and rng.random() < ENGLISH_SHARE
        tools.scenario = make_scenario(area, rng)
        question = _question(kind, english, area, tools.scenario, rng)
        captured.clear()
        try:
            result = agent.ask(question, tools.scenario.now)
        except AttributeError:
            # The agent did not see a district (national summary, dam, river) and asked for a tool this
            # generator does not fake. Those paths never call the LLM, so there is nothing to learn from them.
            stats["skip:agent_left_district_path"] += 1
            continue
        # Only district answers: the agent also sends data answers (waves, storms, dams, ...) to the explainer,
        # but their facts have no hazard lines, so the answers built here would be empty or wrong.
        if "facts" not in captured or result.intent not in ("risk", "explain"):
            stats[f"skip:agent_intent_{result.intent}"] += 1
            continue
        facts_text = captured["facts"]
        facts = parse_facts(facts_text)
        asked = list(result.hazards)
        if english:
            answer = answer_en(facts, kind, asked, _english_label(area, gazetteer), tools.scenario, rng)
        else:
            answer = answer_th(facts, kind, asked, rng)
        problems = validate(answer, facts_text, question)
        if english and THAI.search(answer):
            problems.append("thai text in an english answer")
        if len(answer.splitlines()) > MAX_LINES:
            problems.append("too many lines")
        if problems:
            stats["skip:" + problems[0].split(" (")[0].split(":")[0]] += 1
            continue
        inp = INPUT_TEMPLATE.format(question=question, facts=facts_text)
        if inp in seen:
            stats["skip:duplicate"] += 1
            continue
        seen.add(inp)
        stats[f"kind:{kind}{':en' if english else ''}"] += 1
        samples.append({"instruction": SYSTEM_INSTRUCTION, "input": inp, "output": answer})
    return samples


def split_areas(gazetteer: Gazetteer, test_share: float, seed: int) -> tuple[list[Area], list[Area]]:
    """Districts for training and districts held out for testing."""
    districts = sorted((a for a in gazetteer.areas if a.level == "amphoe"), key=lambda a: a.code)
    random.Random(seed).shuffle(districts)
    n_test = max(1, int(len(districts) * test_share))
    return districts[n_test:], districts[:n_test]


def main():
    parser = argparse.ArgumentParser(description="Generate the explainer dataset from the real agent's facts")
    parser.add_argument("--count", type=int, default=3000, help="Training samples")
    parser.add_argument("--test-count", type=int, default=500, help="Test samples, from held-out districts")
    parser.add_argument("--gazetteer", type=str, default="data/processed/gazetteer.json", help="Built by `make data`")
    parser.add_argument("--out-dir", type=str, default="07_knowledge_decision_llm/training/data")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    gazetteer = Gazetteer.load(Path(args.gazetteer))
    train_areas, test_areas = split_areas(gazetteer, test_share=0.15, seed=args.seed)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for name, areas, count, seed in (("train", train_areas, args.count, args.seed), ("test", test_areas, args.test_count, args.seed + 1)):
        stats: Counter = Counter()
        samples = generate_explainer_dataset(gazetteer, areas, count, seed=seed, stats=stats)
        path = out_dir / f"{name}_explainer.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            for s in samples:
                f.write(json.dumps(s, ensure_ascii=False) + "\n")
        print(f"{name}: {len(samples)} samples from {len(areas)} districts -> {path}")
        for key, n in sorted(stats.items()):
            print(f"  {key}: {n}")


if __name__ == "__main__":
    main()
