import json

from training.train_qwen_lora import stale_explainer_data

INSTRUCTION = "คุณคือผู้ช่วยของระบบเตือนภัย"
FACTS = "\n\nข้อเท็จจริง (ห้ามเปลี่ยน):\nอ.บ้านหลวง จ.น่าน: แผ่นดินไหวระดับปกติ"


def write(path, rows):
    path.write_text("\n".join(json.dumps({"instruction": INSTRUCTION, "input": f"คำถามผู้ใช้: {q}{FACTS}", "output": a},
                                         ensure_ascii=False) for q, a in rows), encoding="utf-8")
    return path


def test_current_dataset_passes(tmp_path):
    rows = [("บ้านหลวงแผ่นดินไหวไหม", "อ.บ้านหลวง จ.น่าน: แผ่นดินไหวระดับปกติ"),
            ("Was there an earthquake near Ban Luang?", "Ban Luang district, Nan: earthquake is NORMAL.")]
    assert stale_explainer_data(write(tmp_path / "train.jsonl", rows)) is None


def test_old_generator_dataset_is_refused(tmp_path):
    # The old generator answered English questions in Thai; a model trained on it did the same.
    rows = [("Was there an earthquake near Ban Luang?", "อ.บ้านหลวง จ.น่าน: แผ่นดินไหวระดับปกติ")]
    assert "answered in Thai" in stale_explainer_data(write(tmp_path / "train.jsonl", rows))
    assert "no English" in stale_explainer_data(write(tmp_path / "thai_only.jsonl", [("บ้านหลวงแผ่นดินไหวไหม", "ปกติ")]))
    assert "empty answer" in stale_explainer_data(write(tmp_path / "empty.jsonl", [("What about Ban Luang?", "")]))
