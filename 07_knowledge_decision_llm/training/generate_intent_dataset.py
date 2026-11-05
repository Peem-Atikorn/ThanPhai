"""Generate instruction-tuning dataset for Question Understanding (Intent & Area extraction).

Saves Question -> JSON pairs for Unsloth / Qwen training:
  train_intent.jsonl
  test_intent.jsonl (>= 500 held-out samples, completely disjoint)
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

# Official DOPA / ThanPhai codes:
# TH1412 = เสนา, TH1406 = บางปะอิน, TH1405 = บางบาล
# TH9011 = หาดใหญ่, TH9001 = เมืองสงขลา
# TH7207 = สองพี่น้อง, TH7204 = บางปลาม้า
# TH1206 = ปากเกร็ด, TH1202 = บางกรวย, TH1204 = บางบัวทอง, TH1205 = ไทรน้อย
DEFAULT_AREAS = [
    {"code": "TH1412", "name_th": "เสนา", "level": "amphoe", "province_th": "พระนครศรีอยุธยา"},
    {"code": "TH1406", "name_th": "บางปะอิน", "level": "amphoe", "province_th": "พระนครศรีอยุธยา"},
    {"code": "TH1405", "name_th": "บางบาล", "level": "amphoe", "province_th": "พระนครศรีอยุธยา"},
    {"code": "TH9011", "name_th": "หาดใหญ่", "level": "amphoe", "province_th": "สงขลา"},
    {"code": "TH9001", "name_th": "เมืองสงขลา", "level": "amphoe", "province_th": "สงขลา"},
    {"code": "TH7207", "name_th": "สองพี่น้อง", "level": "amphoe", "province_th": "สุพรรณบุรี"},
    {"code": "TH7204", "name_th": "บางปลาม้า", "level": "amphoe", "province_th": "สุพรรณบุรี"},
    {"code": "TH1206", "name_th": "ปากเกร็ด", "level": "amphoe", "province_th": "นนทบุรี"},
    {"code": "TH1202", "name_th": "บางกรวย", "level": "amphoe", "province_th": "นนทบุรี"},
    {"code": "TH1204", "name_th": "บางบัวทอง", "level": "amphoe", "province_th": "นนทบุรี"},
    {"code": "TH1205", "name_th": "ไทรน้อย", "level": "amphoe", "province_th": "นนทบุรี"},
    {"code": "TH1001", "name_th": "พระนคร", "level": "amphoe", "province_th": "กรุงเทพมหานคร"},
    {"code": "TH5001", "name_th": "เมืองเชียงใหม่", "level": "amphoe", "province_th": "เชียงใหม่"},
    {"code": "TH8301", "name_th": "เมืองภูเก็ต", "level": "amphoe", "province_th": "ภูเก็ต"},
    {"code": "TH4001", "name_th": "เมืองขอนแก่น", "level": "amphoe", "province_th": "ขอนแก่น"},
    {"code": "TH3401", "name_th": "เมืองอุบลราชธานี", "level": "amphoe", "province_th": "อุบลราชธานี"},
    {"code": "TH2001", "name_th": "เมืองชลบุรี", "level": "amphoe", "province_th": "ชลบุรี"},
    {"code": "TH5801", "name_th": "เมืองแม่ฮ่องสอน", "level": "amphoe", "province_th": "แม่ฮ่องสอน"},
    {"code": "TH5701", "name_th": "เมืองเชียงราย", "level": "amphoe", "province_th": "เชียงราย"},
    {"code": "TH7101", "name_th": "เมืองกาญจนบุรี", "level": "amphoe", "province_th": "กาญจนบุรี"},
    {"code": "TH8001", "name_th": "เมืองนครศรีธรรมราช", "level": "amphoe", "province_th": "นครศรีธรรมราช"},
]

DEFAULT_PROVINCES = [
    {"code": "TH14", "name_th": "พระนครศรีอยุธยา", "level": "province", "province_th": "พระนครศรีอยุธยา"},
    {"code": "TH90", "name_th": "สงขลา", "level": "province", "province_th": "สงขลา"},
    {"code": "TH72", "name_th": "สุพรรณบุรี", "level": "province", "province_th": "สุพรรณบุรี"},
    {"code": "TH12", "name_th": "นนทบุรี", "level": "province", "province_th": "นนทบุรี"},
    {"code": "TH10", "name_th": "กรุงเทพมหานคร", "level": "province", "province_th": "กรุงเทพมหานคร"},
    {"code": "TH50", "name_th": "เชียงใหม่", "level": "province", "province_th": "เชียงใหม่"},
    {"code": "TH83", "name_th": "ภูเก็ต", "level": "province", "province_th": "ภูเก็ต"},
    {"code": "TH40", "name_th": "ขอนแก่น", "level": "province", "province_th": "ขอนแก่น"},
    {"code": "TH34", "name_th": "อุบลราชธานี", "level": "province", "province_th": "อุบลราชธานี"},
    {"code": "TH20", "name_th": "ชลบุรี", "level": "province", "province_th": "ชลบุรี"},
    {"code": "TH58", "name_th": "แม่ฮ่องสอน", "level": "province", "province_th": "แม่ฮ่องสอน"},
    {"code": "TH57", "name_th": "เชียงราย", "level": "province", "province_th": "เชียงราย"},
    {"code": "TH71", "name_th": "กาญจนบุรี", "level": "province", "province_th": "กาญจนบุรี"},
    {"code": "TH80", "name_th": "นครศรีธรรมราช", "level": "province", "province_th": "นครศรีธรรมราช"},
]

INSTRUCTION_PROMPT = (
    "คุณคือระบบทำความเข้าใจคำถามด้านภัยพิบัติของประเทศไทย ให้สกัดข้อมูลจากคำถามและตอบกลับเป็น JSON "
    "ที่มีโครงสร้าง: intent (risk, explain, prepare, emergency, dam, river, out_of_scope), "
    "hazards (รายชื่อภัย), areas (รหัสและชื่อพื้นที่), time_window (days, future)"
)

OUT_OF_SCOPE_QUESTIONS = [
    "วันนี้กินอะไรดี",
    "ขอสูตรทำต้มยำกุ้งหน่อย",
    "ราคาน้ำมันวันนี้ขึ้นไหม",
    "ผลบอลพรีเมียร์ลีกเมื่อคืนเป็นยังไง",
    "สอนเขียนโค้ด Python หน่อย",
    "แนะนำสถานที่เที่ยววันหยุด",
    "หวยงวดนี้ออกอะไร",
    "สวัสดีครับ สบายดีไหม",
    "คุณชื่ออะไร ทำอะไรได้บ้าง",
    "เปิดเพลงให้ฟังหน่อย",
    "หุ้นวันนี้บวกหรือลบ",
    "อัตราแลกเปลี่ยนดอลลาร์เป็นบาทเท่าไหร่",
    "วิธีสมัครสอบ ก.พ. ทำยังไง",
    "ร้านกาแฟแถวสยามร้านไหนอร่อย",
    "วันหยุดราชการเดือนหน้ามีวันไหนบ้าง",
    "iPhone รุ่นใหม่ออกเมื่อไหร่",
    "ขอวิธีลดน้ำหนักแบบเร่งด่วน",
    "แมวพันธุ์ไหนเลี้ยงง่ายสุด",
    "หนังเข้าใหม่สัปดาห์นี้มีเรื่องอะไรบ้าง",
    "สอนคำศัพท์ภาษาอังกฤษสำหรับสัมภาษณ์งาน",
    "วิธีจองตั๋วรถไฟออนไลน์",
    "โปรโมชันตั๋วเครื่องบินไปญี่ปุ่นช่วงนี้มีไหม",
    "สภาพอากาศที่ลอนดอนเป็นอย่างไร",
    "แนะนำร้านอาหารครอบครัวแถวบางนา",
    "วิธีแก้ปวดหัวไมเกรนเบื้องต้น",
    "การจัดโต๊ะทำงานตามหลักฮวงจุ้ย",
    "วิธีล้างแอร์บ้านด้วยตัวเอง",
    "เกมมือถือน่าเล่นประจำเดือนนี้",
    "ตรวจสลากกินแบ่งรัฐบาลย้อนหลัง",
    "วิธีทำไข่เจียวให้กรอบฟู",
    "ขอคำอวยพรวันเกิดเพื่อนสนิท",
    "ราคาผักสดในตลาดไทวันนี้",
    "สรุปข่าวเด่นประจำวันให้ฟังหน่อย",
    "แนะนำซีรีส์เกาหลีที่น่าดูใน Netflix",
    "วิธีปลูกต้นไม้ในคอนโด",
    "วิธีทำพาสปอร์ตใหม่ต้องเตรียมเอกสารอะไรบ้าง",
    "รถไฟฟ้าสายสีชมพูเปิดกี่โมง",
    "สอนวิธีชงกาแฟดริปหน่อย",
    "หมาไม่ยอมกินข้าวทำยังไงดี",
    "อาการนอนไม่หลับเกิดจากอะไร",
    "แนะนำหนังสือพัฒนาตนเองน่าอ่าน",
    "งานหนังสือจัดที่ไหนถึงวันไหน",
    "วิธีผูกเนคไทแบบง่าย",
    "วิธีทำ resume ภาษาอังกฤษ",
    "แนะนำหูฟังไร้สายราคาประหยัด",
    "รถไฟฟ้าใต้ดินปิดกี่โมง",
    "วิธีคำนวณภาษีเงินได้บุคคลธรรมดา",
    "ร้านตัดผมชายสไตล์วินเทจแถวลาดพร้าว",
    "วิธีทำขนมปังอบเนยสด",
    "สอนเล่นกีตาร์คอร์ดง่ายๆ หน่อย",
]


def load_gazetteer_areas(gazetteer_path: Path) -> tuple[list[dict], list[dict]]:
    """Loads gazetteer and separates amphoes from provinces with parent province lookup."""
    if gazetteer_path.exists():
        try:
            data = json.loads(gazetteer_path.read_text(encoding="utf-8"))
            # 1. Map provinces
            prov_map = {
                item["code"]: item["name_th"]
                for item in data
                if item.get("level") == "province"
            }
            provinces = [
                {
                    "code": item["code"],
                    "name_th": item["name_th"],
                    "level": "province",
                    "province_th": item["name_th"],
                }
                for item in data
                if item.get("level") == "province"
            ]
            # 2. Map amphoes
            amphoes = [
                {
                    "code": item["code"],
                    "name_th": item["name_th"],
                    "level": "amphoe",
                    "province_th": prov_map.get(item.get("parent_code"), item["name_th"]),
                }
                for item in data
                if item.get("level") == "amphoe"
            ]
            if amphoes and provinces:
                return amphoes, provinces
        except Exception:
            pass
    return DEFAULT_AREAS, DEFAULT_PROVINCES


def generate_intent_samples(
    amphoe_areas: list[dict], province_areas: list[dict], count: int = 10000, seed: int = 42
) -> list[dict]:
    random.seed(seed)
    seen_questions: set[str] = set()
    samples: list[dict] = []

    def add_sample(question: str, output_obj: dict):
        q = question.strip()
        if q not in seen_questions:
            seen_questions.add(q)
            samples.append({
                "instruction": INSTRUCTION_PROMPT,
                "input": q,
                "output": json.dumps(output_obj, ensure_ascii=False),
            })

    # 1. Risk questions on Amphoes
    risk_amphoe_templates = [
        "{time}{area}{hazard}ไหม",
        "{area}ตอนนี้{hazard}หรือยัง",
        "{time}มีเตือนภัย{hazard}ที่{area}หรือเปล่า",
        "สถานการณ์{hazard}ที่{area}{time}เป็นอย่างไรบ้าง",
        "{area}{time}มีโอกาสเกิด{hazard}ไหมครับ",
        "{time}ที่{area}ระดับ{hazard}รุนแรงไหม",
    ]
    for _ in range(int(count * 0.45)):
        area = random.choice(amphoe_areas)
        hazard_key = random.choice(list(HAZARDS_INFO.keys()))
        hazard_word = random.choice(HAZARDS_INFO[hazard_key])
        time_word, time_meta = random.choice(TIME_PATTERNS)
        template = random.choice(risk_amphoe_templates)

        area_str = random.choice([
            area["name_th"],
            f"อำเภอ{area['name_th']}",
            f"อ.{area['name_th']}",
            f"อ.{area['name_th']} จ.{area['province_th']}",
        ])
        question = template.format(time=time_word, area=area_str, hazard=hazard_word)
        out = {
            "intent": "risk",
            "hazards": [hazard_key],
            "areas": [{"code": area["code"], "name_th": area["name_th"], "level": "amphoe"}],
            "time_window": time_meta,
        }
        add_sample(question, out)

    # 2. Risk questions on Provinces (tagged with province code!)
    risk_prov_templates = [
        "จ.{province}{time}{hazard}ไหม",
        "จังหวัด{province}{time}มีเตือนภัย{hazard}หรือเปล่า",
        "{time}{province}เสี่ยง{hazard}ระดับไหน",
        "สถานการณ์{hazard}ในจังหวัด{province}ตอนนี้",
    ]
    for _ in range(int(count * 0.20)):
        prov = random.choice(province_areas)
        hazard_key = random.choice(list(HAZARDS_INFO.keys()))
        hazard_word = random.choice(HAZARDS_INFO[hazard_key])
        time_word, time_meta = random.choice(TIME_PATTERNS)
        template = random.choice(risk_prov_templates)

        question = template.format(time=time_word, province=prov["name_th"], hazard=hazard_word)
        out = {
            "intent": "risk",
            "hazards": [hazard_key],
            "areas": [{"code": prov["code"], "name_th": prov["name_th"], "level": "province"}],
            "time_window": time_meta,
        }
        add_sample(question, out)

    # 3. Explain questions
    explain_templates = [
        "ทำไม{area}ถึงแจ้งเตือน{level}",
        "เพราะอะไร{area}ถึงประกาศระดับ{level}",
        "{area}ทำไมถึงมีความเสี่ยง{hazard}ระดับ{level}",
    ]
    levels = ["เฝ้าระวัง", "เตือนภัย", "วิกฤต"]
    for _ in range(int(count * 0.10)):
        area = random.choice(amphoe_areas)
        hazard_key = random.choice(list(HAZARDS_INFO.keys()))
        level = random.choice(levels)
        template = random.choice(explain_templates)
        question = template.format(
            area=f"อำเภอ{area['name_th']}",
            hazard=random.choice(HAZARDS_INFO[hazard_key]),
            level=level,
        )
        out = {
            "intent": "explain",
            "hazards": [hazard_key],
            "areas": [{"code": area["code"], "name_th": area["name_th"], "level": "amphoe"}],
            "time_window": {"days": 1, "future": False},
        }
        add_sample(question, out)

    # 4. Prepare questions
    prepare_templates = [
        "วิธีเตรียมตัวรับมือ{hazard}",
        "ต้องเตรียมอะไรบ้างเมื่อเกิด{hazard}",
        "มีแนวทางรับมือ{hazard}อย่างไร",
        "ต้องอพยพอย่างไรเมื่อมี{hazard}",
        "คำแนะนำการเตรียมถุงยังชีพสำหรับ{hazard}",
    ]
    for _ in range(int(count * 0.10)):
        hazard_key = random.choice(list(HAZARDS_INFO.keys()))
        hazard_word = random.choice(HAZARDS_INFO[hazard_key])
        template = random.choice(prepare_templates)
        question = template.format(hazard=hazard_word)
        out = {
            "intent": "prepare",
            "hazards": [hazard_key],
            "areas": [],
            "time_window": {"days": 3, "future": False},
        }
        add_sample(question, out)

    # 5. Emergency questions
    emergency_templates = [
        "ช่วยด้วย น้ำท่วมบ้านมิดหลังคาแล้ว ที่อำเภอ{area}",
        "มีคนติดอยู่ในบ้านที่{area} ระดับน้ำสูงมาก ช่วยด้วย",
        "ฉุกเฉิน เกิดดินถล่มทับเส้นทางที่{area} มีผู้บาดเจ็บ",
    ]
    for _ in range(int(count * 0.05)):
        area = random.choice(amphoe_areas)
        template = random.choice(emergency_templates)
        question = template.format(area=area["name_th"])
        out = {
            "intent": "emergency",
            "hazards": ["flood" if "น้ำ" in question else "landslide"],
            "areas": [{"code": area["code"], "name_th": area["name_th"], "level": "amphoe"}],
            "time_window": {"days": 1, "future": False},
        }
        add_sample(question, out)

    # 6. Out of scope questions (diverse set)
    for q in OUT_OF_SCOPE_QUESTIONS:
        out = {
            "intent": "out_of_scope",
            "hazards": [],
            "areas": [],
            "time_window": {"days": 3, "future": False},
        }
        add_sample(q, out)

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

    amphoes, provinces = load_gazetteer_areas(Path(args.gazetteer))
    samples = generate_intent_samples(amphoes, provinces, count=args.count)

    test_count = max(500, min(args.test_count, len(samples) // 3))
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

    print(f"Generated {len(train_samples)} training and {len(test_samples)} test samples (100% deduplicated).")
    print(f"Saved to {train_path} and {test_path}")


if __name__ == "__main__":
    main()
