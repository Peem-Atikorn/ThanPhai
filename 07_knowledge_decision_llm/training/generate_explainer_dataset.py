"""Generate and filter safe instruction-tuning dataset for Facts Explanation.

Uses the official decision_engine.explainer.validate() to ensure 100% of generated pairs
have zero hallucinated numbers, zero modified hazard levels, and zero ungrounded advice.
Strictly conforms to rules.yaml thresholds and SYSTEM_INSTRUCTION rules.

Saves:
  train_explainer.jsonl
  test_explainer.jsonl (>= 500 held-out samples)
"""

import argparse
import json
import random
from pathlib import Path

from decision_engine.explainer import SYSTEM_INSTRUCTION, validate

# Aligned with decision_engine/rules.yaml
HAZARD_FACT_SPECS = [
    ("น้ำท่วม", "flood", [
        ("ปกติ", "ระดับน้ำต่ำกว่าตลิ่ง {v:.2f} ม.", (0.31, 3.0)),
        ("เฝ้าระวัง", "ระดับน้ำใกล้ตลิ่งเหลือ {v:.2f} ม.", (0.0, 0.30)),
        ("เตือนภัย", "ระดับน้ำล้นตลิ่ง {v:.2f} ม.", (0.01, 0.49)),
        ("วิกฤต", "ระดับน้ำล้นตลิ่งวิกฤต {v:.2f} ม.", (0.50, 3.5)),
    ]),
    ("ฝนหนัก/พายุ", "rain_storm", [
        ("ปกติ", "ฝนสะสม 24 ชม. {v:.1f} มม.", (0.0, 35.0)),
        ("เฝ้าระวัง", "ฝนสะสม 24 ชม. {v:.1f} มม. (ฝนหนัก)", (35.1, 90.0)),
        ("เตือนภัย", "ฝนสะสม 24 ชม. {v:.1f} มม. (ฝนหนักมาก)", (90.1, 200.0)),
    ]),
    ("ไฟป่า/ไฟหญ้า", "fire", [
        ("ปกติ", "จุดความร้อน {v} จุด", (0, 2)),
        ("เฝ้าระวัง", "จุดความร้อนสะสม {v} จุด", (3, 9)),
        ("เตือนภัย", "จุดความร้อนสะสม {v} จุด", (10, 40)),
    ]),
    ("ภัยแล้ง", "drought", [
        ("ปกติ", "ดัชนีฝนแล้ง SPI-3 {v:.2f}", (-0.99, 1.5)),
        ("เฝ้าระวัง", "ดัชนีฝนแล้ง SPI-3 {v:.2f} (เริ่มแล้ง)", (-1.49, -1.0)),
        ("เตือนภัย", "ดัชนีฝนแล้ง SPI-3 {v:.2f} (แล้งรุนแรง)", (-1.99, -1.5)),
        ("วิกฤต", "ดัชนีฝนแล้ง SPI-3 {v:.2f} (แล้งจัดผิดปกติ)", (-3.0, -2.0)),
    ]),
    ("ฝุ่น PM2.5", "air_quality", [
        ("ปกติ", "ค่าฝุ่น PM2.5 {v:.1f} มคก./ลบ.ม.", (5.0, 37.5)),
        ("เฝ้าระวัง", "ค่าฝุ่น PM2.5 {v:.1f} มคก./ลบ.ม. (เริ่มมีผลต่อสุขภาพ)", (37.6, 75.0)),
        ("เตือนภัย", "ค่าฝุ่น PM2.5 {v:.1f} มคก./ลบ.ม. (มีผลกระทบต่อสุขภาพ)", (75.1, 150.0)),
    ]),
    ("ความร้อนจัด", "heat", [
        ("ปกติ", "ดัชนีความร้อน {v:.1f} °C", (27.0, 39.3)),
        ("เฝ้าระวัง", "ดัชนีความร้อน {v:.1f} °C", (39.4, 41.9)),
        ("เตือนภัย", "ดัชนีความร้อน {v:.1f} °C", (42.0, 51.9)),
        ("วิกฤต", "ดัชนีความร้อน {v:.1f} °C", (52.0, 58.0)),
    ]),
    ("แผ่นดินไหว", "earthquake", [
        ("ปกติ", "ไม่พบแรงสั่นสะเทือนที่มีนัยสำคัญ", None),
        ("เฝ้าระวัง", "แรงสั่นสะเทือนระดับรู้สึกได้ {v:.1f} MMI", (4.0, 5.9)),
        ("เตือนภัย", "แรงสั่นสะเทือนรุนแรง {v:.1f} MMI", (6.0, 6.9)),
        ("วิกฤต", "แรงสั่นสะเทือนเสียหาย {v:.1f} MMI", (7.0, 8.5)),
    ]),
    ("คลื่นลมแรง", "marine", [
        ("ปกติ", "คลื่นสูง {v:.1f} ม.", (0.5, 1.9)),
        ("เฝ้าระวัง", "คลื่นสูง {v:.1f} ม. (เรือเล็กระวัง)", (2.0, 2.9)),
        ("เตือนภัย", "คลื่นสูง {v:.1f} ม. (คลื่นจัด)", (3.0, 3.9)),
        ("วิกฤต", "คลื่นสูง {v:.1f} ม. (ทะเลมีคลื่นจัดมาก)", (4.0, 6.0)),
    ]),
    ("พายุหมุนเขตร้อน", "tropical_cyclone", [
        ("ปกติ", "ไม่มีพายุหมุนเขตร้อนในระยะส่งผลกระทบ", None),
        ("เฝ้าระวัง", "อยู่ในรัศมีขอบพายุ ความเร็วลม {v} กม./ชม.", (63, 92)),
        ("เตือนภัย", "อยู่ในรัศมีลมแรงพายุ ความเร็วลม {v} กม./ชม.", (93, 118)),
        ("วิกฤต", "อยู่ในรัศมีลมแรงพายุไต้ฝุ่น ความเร็วลม {v} กม./ชม.", (119, 180)),
    ]),
    ("ดินถล่ม", "landslide", [
        ("ปกติ", "พื้นที่ไม่มีสัญญาณดินถล่ม", None),
        ("เฝ้าระวัง", "พยากรณ์ฝน 24 ชม. {v:.1f} มม. ในพื้นที่ลาดชันเสี่ยง", (100.1, 150.0)),
        ("เตือนภัย", "ฝนสะสม 24 ชม. {v:.1f} มม. เสี่ยงดินถล่ม", (100.1, 150.0)),
        ("วิกฤต", "ฝนสะสม 24 ชม. {v:.1f} มม. เสี่ยงดินถล่มรุนแรง", (150.1, 250.0)),
    ]),
]

SAMPLE_AREAS = [
    ("อำเภอเสนา", "พระนครศรีอยุธยา"),
    ("อำเภอบางบาล", "พระนครศรีอยุธยา"),
    ("อำเภอบางปะอิน", "พระนครศรีอยุธยา"),
    ("อำเภอหาดใหญ่", "สงขลา"),
    ("อำเภอเมืองสงขลา", "สงขลา"),
    ("อำเภอสองพี่น้อง", "สุพรรณบุรี"),
    ("อำเภอบางปลาม้า", "สุพรรณบุรี"),
    ("อำเภอปากเกร็ด", "นนทบุรี"),
    ("อำเภอบางกรวย", "นนทบุรี"),
    ("อำเภอบางบัวทอง", "นนทบุรี"),
    ("อำเภอเมืองเชียงใหม่", "เชียงใหม่"),
    ("อำเภอเมืองภูเก็ต", "ภูเก็ต"),
    ("อำเภอเมืองขอนแก่น", "ขอนแก่น"),
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

        # Construct concise safe answer sentence without emergency numbers (appended by system per rule 4)
        if num_str:
            answer_parts.append(f"{th_label}อยู่ในระดับ{lvl} ({detail})")
        else:
            answer_parts.append(f"{th_label}ระดับ{lvl} ({detail})")

    fact_lines.append("หากอยู่ในอันตราย โทร 1784 (ปภ.) · 1669 (เจ็บป่วยฉุกเฉิน) · 199 (ดับเพลิง) ทันที")
    facts_text = "\n".join(fact_lines)

    question = f"สถานการณ์ที่{area_name}เป็นอย่างไรบ้าง"
    # Strict rule 4: Do not include emergency contacts or advice in rewritten answer
    answer_text = " ".join(answer_parts)

    # Strict check using validate()
    problems = validate(answer_text, facts_text, question)
    if not problems:
        return question, facts_text, answer_text
    return None


def generate_explainer_dataset(target_count: int = 3000, seed: int = 42) -> list[dict]:
    random.seed(seed)
    samples = []
    seen_inputs = set()
    attempts = 0
    max_attempts = target_count * 5

    while len(samples) < target_count and attempts < max_attempts:
        attempts += 1
        area_name, prov_name = random.choice(SAMPLE_AREAS)
        item = generate_single_example(area_name, prov_name)
        if item is not None:
            question, facts, answer = item
            inp = f"คำถามผู้ใช้: {question}\n\nข้อเท็จจริง (ห้ามเปลี่ยน):\n{facts}"
            if inp not in seen_inputs:
                seen_inputs.add(inp)
                samples.append({
                    "instruction": SYSTEM_INSTRUCTION,
                    "input": inp,
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

    test_count = max(500, min(args.test_count, len(samples) // 3))
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

    print(f"Successfully generated {len(train_samples)} training and {len(test_samples)} test samples (100% deduplicated).")
    print(f"Verified 100% Zero Defects against validate(). Saved to {train_path} and {test_path}")


if __name__ == "__main__":
    main()
