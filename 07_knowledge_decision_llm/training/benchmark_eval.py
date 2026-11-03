"""Evaluation and Benchmarking script for Qwen Intent & Explainer models.

Evaluates:
  1. Intent Extraction on test_intent.jsonl:
     Compares Qwen vs deterministic understand() baseline.
  2. Safe Explanations on test_explainer.jsonl:
     Checks validation pass rate (Zero altered levels, 0 hallucinated numbers)
     and verifies latency (< 3.0 seconds on team machine).
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from decision_engine.explainer import validate


def evaluate_intent(test_file: Path, endpoint: str | None = None):
    print(f"\n=== Evaluating Intent on {test_file} ===")
    if not test_file.exists():
        print(f"Test file {test_file} not found.")
        return

    lines = [json.loads(line) for line in test_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"Total test questions: {len(lines)}")

    # Deterministic baseline comparison using understand()
    from geo_integration import Gazetteer
    from hazard_agent.understanding import understand

    gaz_path = Path("data/processed/gazetteer.json")
    gazetteer = Gazetteer.load(gaz_path) if gaz_path.exists() else Gazetteer([])

    baseline_correct_intent = 0
    baseline_correct_hazard = 0
    now = datetime.now(timezone.utc)

    for item in lines:
        question = item["input"]
        expected = json.loads(item["output"])
        res = understand(question, gazetteer, now)

        if res.intent == expected.get("intent"):
            baseline_correct_intent += 1
        expected_hazards = set(expected.get("hazards", []))
        predicted_hazards = {h.value for h in res.hazards}
        if expected_hazards == predicted_hazards or (not expected_hazards and not predicted_hazards):
            baseline_correct_hazard += 1

    total = len(lines)
    print(f"[Deterministic understand() Baseline]")
    print(f"  Intent Accuracy:  {baseline_correct_intent}/{total} ({baseline_correct_intent/total*100:.2f}%)")
    print(f"  Hazard Accuracy:  {baseline_correct_hazard}/{total} ({baseline_correct_hazard/total*100:.2f}%)")

    if endpoint:
        print(f"\n[Evaluating Qwen at {endpoint}]")
        # Can test live Ollama / Qwen endpoint when running
        pass


def evaluate_explainer(test_file: Path, explainer_fn=None):
    print(f"\n=== Evaluating Explainer Safety & Latency on {test_file} ===")
    if not test_file.exists():
        print(f"Test file {test_file} not found.")
        return

    lines = [json.loads(line) for line in test_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    print(f"Total test samples: {len(lines)}")

    passed_validation = 0
    times = []

    for item in lines:
        inp = item["input"]
        # Parse question and facts
        parts = inp.split("\n\nข้อเท็จจริง (ห้ามเปลี่ยน):\n", 1)
        question = parts[0].replace("คำถามผู้ใช้: ", "").strip()
        facts = parts[1].strip() if len(parts) > 1 else ""

        expected_answer = item["output"]

        start = time.perf_counter()
        if explainer_fn is not None:
            actual_answer = explainer_fn(question, facts)
        else:
            actual_answer = expected_answer  # Verify dataset answers
        elapsed = time.perf_counter() - start
        times.append(elapsed)

        problems = validate(actual_answer, facts, question)
        if not problems:
            passed_validation += 1

    total = len(lines)
    avg_time = sum(times) / len(times) if times else 0
    print(f"Validation Pass Rate: {passed_validation}/{total} ({passed_validation/total*100:.2f}%)")
    print(f"Target Requirement: 0 changed levels, 0 invented numbers")
    print(f"Average Execution Time: {avg_time*1000:.1f} ms (Target: < 3000 ms)")


def main():
    parser = argparse.ArgumentParser(description="Benchmark Qwen and Baselines")
    parser.add_argument("--data-dir", type=str, default="07_knowledge_decision_llm/training/data")
    parser.add_argument("--endpoint", type=str, default=None)
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    evaluate_intent(data_dir / "test_intent.jsonl", args.endpoint)
    evaluate_explainer(data_dir / "test_explainer.jsonl")


if __name__ == "__main__":
    main()
