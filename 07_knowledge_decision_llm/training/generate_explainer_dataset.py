"""Generate and filter safe instruction-tuning dataset for Facts Explanation.

Uses the official decision_engine.explainer.validate() to ensure 100% of generated pairs
have zero hallucinated numbers, zero modified hazard levels, and zero ungrounded advice.

Saves:
  train_explainer.jsonl
  test_explainer.jsonl (>= 500 held-out samples)
"""

import argparse
import json
import random
from pathlib import Path

from decision_engine.explainer import SYSTEM_INSTRUCTION, validate

HAZARD_FACT_SPECS = [
    ("น้ำท่วม", "flood", [
        ("ปกติ", "ระดับน้ำต่ำกว่าตลิ่ง {v:.2f} ม.", (0.5, 3.0)),
        ("เฝ้าระวัง", "ระดับน้ำใกล้ตลิ่งเหลือ {v:.2f} ม.", (0.1, 0.49)),
        ("เตือนภัย", "ระดับน้ำล้นตลิ่ง {v:.2f} ม.", (0.1, 0.8)),
        ("วิกฤต", "ระดับน้ำล้นตลิ่งวิกฤต {v:.2f} ม.", (1.0, 3.5)),
    ]),
    ("ฝนหนัก/พายุ", "rain_storm", [
        ("ปกติ", "ฝนสะสม 24 ชม. {v:.1f} มม.", (0.0, 35.0)),
        ("เฝ้าระวัง", "ฝนสะสม 24 ชม. {v:.1f} มม.", (35.1, 89.9)),
        ("เตือนภัย", "ฝนสะสม 24 ชม. {v:.1f} มม.", (90.0, 140.0)),
        ("วิกฤต", "ฝนสะสม 24 ชม. {v:.1f} มม. ฝนตกหนักมาก", (140.1, 250.0)),
    ]),
    ("ไฟป่า/ไฟหญ้า", "fire", [
        ("ปกติ", "จุดความร้อน {v} จุด", (0, 0)),
        ("เฝ้าระวัง", "จุดความร้อนสะสม {v} จุด", (1, 4)),
        ("เตือนภัย", "จุดความร้อนสะสม {v} จุด", (5, 15)),
        ("วิกฤต", "จุดความร้อนสะสมหนาแน่น {v} จุด", (16, 50)),
    ]),
    ("ภัยแล้ง", "drought", [
        ("ปกติ", "ดัชนีฝนแล้ง SPI {v:.2f}", (-0.99, 1.5)),
        ("เฝ้าระวัง", "ดัชนีฝนแล้ง SPI {v:.2f} เริ่มแล้ง", (-1.49, -1.0)),
        ("เตือนภัย", "ดัชนีฝนแล้ง SPI {v:.2f} แล้งรุนแรง", (-1.99, -1.5)),
        ("วิกฤต", "ดัชนีฝนแล้ง SPI {v:.2f} แล้งจัดผิดปกติ", (-3.0, -2.0)),
    ]),
    ("ฝุ่น PM2.5", "air_quality", [
        ("ปกติ", "ค่าฝุ่น {v:.1f} มคก./ลบ.ม.", (5.0, 37.5)),
        ("เฝ้าระวัง", "ค่าฝุ่น {v:.1f} มคก./ลบ.ม. เริ่มมีผลต่อสุขภาพ", (37.6, 75.0)),
        ("เตือนภัย", "ค่าฝุ่น {v:.1f} มคก./ลบ.ม. มีผลกระทบต่อสุขภาพ", (75.1, 150.0)),
    ]),
    ("ความร้อนจัด", "heat", [
        ("ปกติ", "ดัชนีความร้อน {v:.1f} °C", (27.0, 39.3)),
        ("เฝ้าระวัง", "ดัชนีความร้อน {v:.1f} °C ระดับอันตราย", (39.4, 51.9)),
        ("เตือนภัย", "ดัชนีความร้อน {v:.1f} °C ระดับอันตรายยิ่ง", (52.0, 58.0)),
    ]),
    ("แผ่นดินไหว", "earthquake", [
        ("ปกติ", "ไม่พบแรงสั่นสะเทือนที่มีนัยสำคัญ", None),
        ("เฝ้าระวัง", "แรงสั่นสะเทือนระดับรู้สึกได้ {v:.1f} แมกนิจูด", (3.0, 4.5)),
        ("เตือนภัย", "แรงสั่นสะเทือนรุนแรง {v:.1f} แมกนิจูด", (4.6, 6.5)),
    ]),
    ("คลื่นลมแรง", "marine", [
        ("ปกติ", "คลื่นสูง {v:.1f} ม.", (0.5, 1.9)),
        ("เฝ้าระวัง", "คลื่นสูง {v:.1f} ม. เรือเล็กระวัง", (2.0, 2.9)),
        ("เตือนภัย", "คลื่นสูง {v:.1f} ม. คลื่นจัด", (3.0, 3.9)),
        ("วิกฤต", "คลื่นสูง {v:.1f} ม. ทะเลมีคลื่นจัดมาก", (4.0, 6.0)),
    ]),
    ("พายุหมุนเขตร้อน", "tropical_cyclone", [
        ("ปกติ", "ไม่มีพายุหมุนเขตร้อนในระยะส่งผลกระทบ", None),
        ("เฝ้าระวัง", "อยู่ในรัศมีขอบพายุ ความเร็วลม {v} กม./ชม.", (40, 60)),
        ("เตือนภัย", "อยู่ในรัศมีลมแรงพายุ ความเร็วลม {v} กม./ชม.", (61, 117)),
    ]),
    ("ดินถล่ม", "landslide", [
        ("ปกติ", "พื้นที่ไม่มีสัญญาณดินถล่ม", None),
        ("เฝ้าระวัง", "พื้นที่ลาดชันเสี่ยง มีฝนสะสม {v:.1f} มม.", (60.0, 99.0)),
        ("เตือนภัย", "ฝนตกหนักต่อเนื่อง {v:.1f} มม. เสี่ยงดินถล่มสูง", (100.0, 200.0)),
    ]),
]

SAMPLE_AREAS = [
    ("อำเภอเสนา", "พระนครศรีอยุธยา"),
    ("อำเภอบางบาล", "พระนครศรีอยุธยา"),
    ("อำเภอหาดใหญ่", "สงขลา"),
    ("อำเภอเมืองเชียงใหม่", "เชียงใหม่"),
    ("อำเภอเมืองภูเก็ต", "ภูเก็ต"),
    ("อำเภอเมืองขอนแก่น", "ขอนแก่น"),
    ("อำเภอสองพี่น้อง", "สุพรรณบุรี"),
    ("อำเภอปากเกร็ด", "นนทบุรี"),
]


def generate_single_example(area_name: str, prov_name: str) -> tuple[str, str, str] | None:
    # Pick 2-4 hazards
    num_hazards = random.choice([2, 3, 4])
    selected_specs = random.sample(HAZARD_FACT_SPECS, num_hazards)

    fact_lines = [f"{area_name} จ.{prov_name} — สถานการณ์ล่าสุดจากข้อมูลตรวจวัด"]
    answer_parts = [f"{area_name}"]

    for th_label, _, levels in selected_specs:
        lvl, detail_tmpl, val_range = random.choice(levels)
        if val_range is None:
            detail = detail_tmpl
            num_str = ""
        elif isinstance(val_range[0], int):
            v = random.randint(val_range[0], val_range[1])
            detail = detail_tmpl.format(v=v)
            num_str = f"{v}"
        else:
            v = round(random.uniform(val_range[0], val_range[1]), 2 if "{v:.2f}" in detail_tmpl else 1)
            detail = detail_tmpl.format(v=v)
            num_str = f"{v}"

        fact_lines.append(f"• {th_label}: {lvl} — {detail}")

        # Construct concise safe answer sentence
        if num_str:
            answer_parts.append(f"{th_label}อยู่ในระดับ{lvl} ({detail})")
        else:
            answer_parts.append(f"{th_label}ระดับ{lvl} ({detail})")

    fact_lines.append("หากอยู่ในอันตราย โทร 1784 (ปภ.) · 1669 (เจ็บป่วยฉุกเฉิน) · 199 (ดับเพลิง) ทันที")
    facts_text = "\n".join(fact_lines)

    question = f"สถานการณ์ที่{area_name}เป็นอย่างไรบ้าง"
    answer_text = " ".join(answer_parts) + " หากเกิดเหตุฉุกเฉินสามารถโทร 1784"

    # Strict check using validate()
    problems = validate(answer_text, facts_text, question)
    if not problems:
        return question, facts_text, answer_text
    return None


def generate_explainer_dataset(target_count: int = 3000, seed: int = 42) -> list[dict]:
    random.seed(seed)
    samples = []
    attempts = 0
    max_attempts = target_count * 5

    while len(samples) < target_count and attempts < max_attempts:
        attempts += 1
        area_name, prov_name = random.choice(SAMPLE_AREAS)
        item = generate_single_example(area_name, prov_name)
        if item is not None:
            question, facts, answer = item
            samples.append({
                "instruction": SYSTEM_INSTRUCTION,
                "input": f"คำถามผู้ใช้: {question}\n\nข้อเท็จจริง (ห้ามเปลี่ยน):\n{facts}",
                "output": answer,
            })

    return samples


def main():
    parser = argparse.ArgumentParser(description="Generate Safe Explainer Dataset")
    parser.add_argument("--count", type=int, default=2500, help="Total samples to generate")
    parser.add_argument("--test-count", type=int, default=500, help="Held-out test samples (>= 500)")
    parser.add_argument("--out-dir", type=str, default="07_knowledge_decision_llm/training/data")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Generating {args.count} safe explainer examples with strict validation...")
    samples = generate_explainer_dataset(target_count=args.count)

    test_count = max(500, args.test_count)
    test_samples = samples[:test_count]
    train_samples = samples[test_count:]

    train_path = out_dir / "train_explainer.jsonl"
    test_path = out_dir / "test_explainer.jsonl"

    with open(train_path, "w", encoding="utf-8") as f:
        for s in train_samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    with open(test_path, "w", encoding="utf-8") as f:
        for s in test_samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    print(f"Successfully generated {len(train_samples)} training and {len(test_samples)} test samples.")
    print(f"Verified 100% Zero Defects against validate(). Saved to {train_path} and {test_path}")


if __name__ == "__main__":
    main()
