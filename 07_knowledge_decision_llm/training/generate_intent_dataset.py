"""Generate instruction-tuning dataset for Question Understanding (Intent & Area extraction).

Saves Question -> JSON pairs for Unsloth / Qwen training:
  train_intent.jsonl
  test_intent.jsonl (>= 500 held-out samples)
"""

import argparse
import json
import random
from pathlib import Path

HAZARDS_INFO = {
    "flood": ["น้ำท่วม", "ท่วม", "น้ำล้นตลิ่ง", "น้ำป่าไหลหลาก"],
    "rain_storm": ["ฝนตกหนัก", "พายุฝน", "ฝนตก", "พายุลมแรง"],
    "drought": ["ภัยแล้ง", "แล้ง", "ขาดแคลนน้ำ", "ฝนทิ้งช่วง"],
    "fire": ["ไฟป่า", "ไฟไหม้ป่า", "จุดความร้อน", "การเผาในที่โล่ง"],
    "air_quality": ["ฝุ่น PM2.5", "ค่าฝุ่น", "หมอกควัน", "มลพิษทางอากาศ", "pm2.5"],
    "heat": ["อากาศร้อนจัด", "ดัชนีความร้อน", "ฮีทสโตรก", "อากาศร้อนมาก"],
    "earthquake": ["แผ่นดินไหว", "แรงสั่นสะเทือน", "อาฟเตอร์ช็อก"],
    "marine": ["คลื่นลมแรง", "คลื่นสูงในทะเล", "เดินเรืออันตราย", "คลื่นทะเล"],
    "tropical_cyclone": ["พายุหมุนเขตร้อน", "พายุดีเปรสชัน", "พายุโซนร้อน", "ไต้ฝุ่น"],
    "landslide": ["ดินถล่ม", "ดินสไลด์", "โคลนถล่ม"],
}

TIME_PATTERNS = [
    ("", {"days": 3, "future": False}),
    ("ตอนนี้", {"days": 1, "future": False}),
    ("วันนี้", {"days": 1, "future": False}),
    ("คืนนี้", {"days": 1, "future": False}),
    ("พรุ่งนี้", {"days": 1, "future": True}),
    ("มะรืนนี้", {"days": 2, "future": True}),
    ("3 วันข้างหน้า", {"days": 3, "future": True}),
    ("สัปดาห์นี้", {"days": 7, "future": True}),
]

DEFAULT_AREAS = [
    {"code": "TH1406", "name_th": "เสนา", "province_th": "พระนครศรีอยุธยา"},
    {"code": "TH1405", "name_th": "บางบาล", "province_th": "พระนครศรีอยุธยา"},
    {"code": "TH1001", "name_th": "พระนคร", "province_th": "กรุงเทพมหานคร"},
    {"code": "TH9001", "name_th": "หาดใหญ่", "province_th": "สงขลา"},
    {"code": "TH5001", "name_th": "เมืองเชียงใหม่", "province_th": "เชียงใหม่"},
    {"code": "TH8301", "name_th": "เมืองภูเก็ต", "province_th": "ภูเก็ต"},
    {"code": "TH4001", "name_th": "เมืองขอนแก่น", "province_th": "ขอนแก่น"},
    {"code": "TH3401", "name_th": "เมืองอุบลราชธานี", "province_th": "อุบลราชธานี"},
    {"code": "TH2001", "name_th": "เมืองชลบุรี", "province_th": "ชลบุรี"},
    {"code": "TH7204", "name_th": "สองพี่น้อง", "province_th": "สุพรรณบุรี"},
    {"code": "TH1202", "name_th": "ปากเกร็ด", "province_th": "นนทบุรี"},
    {"code": "TH5801", "name_th": "เมืองแม่ฮ่องสอน", "province_th": "แม่ฮ่องสอน"},
    {"code": "TH5701", "name_th": "เมืองเชียงราย", "province_th": "เชียงราย"},
    {"code": "TH7101", "name_th": "เมืองกาญจนบุรี", "province_th": "กาญจนบุรี"},
    {"code": "TH8001", "name_th": "เมืองนครศรีธรรมราช", "province_th": "นครศรีธรรมราช"},
]

INSTRUCTION_PROMPT = (
    "คุณคือระบบทำความเข้าใจคำถามด้านภัยพิบัติของประเทศไทย ให้สกัดข้อมูลจากคำถามและตอบกลับเป็น JSON "
    "ที่มีโครงสร้าง: intent (risk, explain, prepare, emergency, dam, river, out_of_scope), "
    "hazards (รายชื่อภัย), areas (รหัสและชื่อพื้นที่), time_window (days, future)"
)


def load_gazetteer_areas(gazetteer_path: Path) -> list[dict]:
    if gazetteer_path.exists():
        try:
            data = json.loads(gazetteer_path.read_text(encoding="utf-8"))
            areas = []
            for item in data:
                areas.append({
                    "code": item.get("code", ""),
                    "name_th": item.get("name_th", ""),
                    "province_th": item.get("province_th", item.get("name_th", "")),
                })
            if areas:
                return areas
        except Exception:
            pass
    return DEFAULT_AREAS


def generate_intent_samples(areas: list[dict], count: int = 10000, seed: int = 42) -> list[dict]:
    random.seed(seed)
    samples = []

    # 1. Risk questions
    risk_templates = [
        "{time}{area}{hazard}ไหม",
        "{area}ตอนนี้{hazard}หรือยัง",
        "{time}มีเตือนภัย{hazard}ที่{area}หรือเปล่า",
        "สถานการณ์{hazard}ที่{area}{time}เป็นอย่างไรบ้าง",
        "{area}{time}มีโอกาสเกิด{hazard}ไหมครับ",
        "{time}ที่{area}ระดับ{hazard}รุนแรงไหม",
    ]

    for _ in range(int(count * 0.65)):
        area = random.choice(areas)
        hazard_key = random.choice(list(HAZARDS_INFO.keys()))
        hazard_word = random.choice(HAZARDS_INFO[hazard_key])
        time_word, time_meta = random.choice(TIME_PATTERNS)
        template = random.choice(risk_templates)

        area_str = random.choice([
            area["name_th"],
            f"อำเภอ{area['name_th']}",
            f"อ.{area['name_th']}",
            f"จ.{area['province_th']}",
            f"{area['name_th']} {area['province_th']}",
        ])

        question = template.format(time=time_word, area=area_str, hazard=hazard_word).strip()
        output_obj = {
            "intent": "risk",
            "hazards": [hazard_key],
            "areas": [area],
            "time_window": time_meta,
        }
        samples.append({
            "instruction": INSTRUCTION_PROMPT,
            "input": question,
            "output": json.dumps(output_obj, ensure_ascii=False),
        })

    # 2. Explain questions
    explain_templates = [
        "ทำไม{area}ถึงแจ้งเตือน{level}",
        "เพราะอะไร{area}ถึงประกาศระดับ{level}",
        "{area}ทำไมถึงมีความเสี่ยง{hazard}ระดับ{level}",
    ]
    levels = ["เฝ้าระวัง", "เตือนภัย", "วิกฤต"]
    for _ in range(int(count * 0.10)):
        area = random.choice(areas)
        hazard_key = random.choice(list(HAZARDS_INFO.keys()))
        level = random.choice(levels)
        template = random.choice(explain_templates)
        question = template.format(
            area=f"อำเภอ{area['name_th']}",
            hazard=random.choice(HAZARDS_INFO[hazard_key]),
            level=level,
        )
        output_obj = {
            "intent": "explain",
            "hazards": [hazard_key],
            "areas": [area],
            "time_window": {"days": 1, "future": False},
        }
        samples.append({
            "instruction": INSTRUCTION_PROMPT,
            "input": question,
            "output": json.dumps(output_obj, ensure_ascii=False),
        })

    # 3. Prepare questions
    prepare_templates = [
        "วิธีเตรียมตัวรับมือ{hazard}",
        "ต้องเตรียมอะไรบ้างเมื่อเกิด{hazard}",
        "มีแนวทางรับมือ{hazard}อย่างไร",
        "ต้องอพยพอย่างไรเมื่อมี{hazard}",
    ]
    for _ in range(int(count * 0.10)):
        hazard_key = random.choice(list(HAZARDS_INFO.keys()))
        hazard_word = random.choice(HAZARDS_INFO[hazard_key])
        template = random.choice(prepare_templates)
        question = template.format(hazard=hazard_word)
        output_obj = {
            "intent": "prepare",
            "hazards": [hazard_key],
            "areas": [],
            "time_window": {"days": 3, "future": False},
        }
        samples.append({
            "instruction": INSTRUCTION_PROMPT,
            "input": question,
            "output": json.dumps(output_obj, ensure_ascii=False),
        })

    # 4. Emergency questions
    emergency_templates = [
        "ช่วยด้วย น้ำท่วมบ้านมิดหลังคาแล้ว ที่{area}",
        "มีคนติดอยู่ในบ้านที่{area} ระดับน้ำสูงมาก ช่วยด้วย",
        "ฉุกเฉิน เกิดดินถล่มทับเส้นทางที่{area} มีผู้บาดเจ็บ",
    ]
    for _ in range(int(count * 0.05)):
        area = random.choice(areas)
        template = random.choice(emergency_templates)
        question = template.format(area=area["name_th"])
        output_obj = {
            "intent": "emergency",
            "hazards": ["flood" if "น้ำ" in question else "landslide"],
            "areas": [area],
            "time_window": {"days": 1, "future": False},
        }
        samples.append({
            "instruction": INSTRUCTION_PROMPT,
            "input": question,
            "output": json.dumps(output_obj, ensure_ascii=False),
        })

    # 5. Out of scope questions
    out_of_scope_samples = [
        "วันนี้กินอะไรดี",
        "ขอสูตรทำต้มยำกุ้งหน่อย",
        "ราคาน้ำมันวันนี้ขึ้นไหม",
        "ผลบอลเมื่อคืนเป็นยังไง",
        "สอนเขียนโค้ด Python หน่อย",
        "แนะนำสถานที่เที่ยววันหยุด",
        "หวยงวดนี้ออกอะไร",
    ]
    for q in out_of_scope_samples:
        output_obj = {
            "intent": "out_of_scope",
            "hazards": [],
            "areas": [],
            "time_window": {"days": 3, "future": False},
        }
        samples.append({
            "instruction": INSTRUCTION_PROMPT,
            "input": q,
            "output": json.dumps(output_obj, ensure_ascii=False),
        })

    random.shuffle(samples)
    return samples


def main():
    parser = argparse.ArgumentParser(description="Generate Question Understanding Dataset")
    parser.add_argument("--count", type=int, default=5000, help="Total number of samples")
    parser.add_argument("--test-count", type=int, default=600, help="Number of test samples (>= 500)")
    parser.add_argument("--out-dir", type=str, default="07_knowledge_decision_llm/training/data")
    parser.add_argument("--gazetteer", type=str, default="data/processed/gazetteer.json")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    areas = load_gazetteer_areas(Path(args.gazetteer))
    samples = generate_intent_samples(areas, count=args.count)

    test_count = max(500, args.test_count)
    test_samples = samples[:test_count]
    train_samples = samples[test_count:]

    train_path = out_dir / "train_intent.jsonl"
    test_path = out_dir / "test_intent.jsonl"

    with open(train_path, "w", encoding="utf-8") as f:
        for s in train_samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    with open(test_path, "w", encoding="utf-8") as f:
        for s in test_samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    print(f"Generated {len(train_samples)} training and {len(test_samples)} test samples.")
    print(f"Saved to {train_path} and {test_path}")


if __name__ == "__main__":
    main()
