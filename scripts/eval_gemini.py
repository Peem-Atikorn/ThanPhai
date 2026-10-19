"""Live check of the Gemini explainer on real questions: how often its answer passes validation, and how fast.

Run from the repo root (needs GEMINI_API_KEY in .env and data/processed/area_snapshot.json from
scripts/live_area_snapshot.py):
    .venv/Scripts/python scripts/eval_gemini.py
Each question costs one Gemini call.
"""

import json
import logging
import os
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
    if re.match(r"^(GEMINI_API_KEY|LLM_MODEL|LLM_THINKING_LEVEL)=.", line):
        key, value = line.split("=", 1)
        os.environ.setdefault(key, value.strip())

from decision_engine.explainer import GeminiExplainer, validate  # noqa: E402
from geo_integration import Gazetteer  # noqa: E402
from hazard_agent import HazardAgent  # noqa: E402

# The free tier allows only a few requests per minute; without a pause the run measures the quota, not the model.
PAUSE_SECONDS = 6

QUESTIONS = [
    "น้ำจะท่วมอำเภอเสนาไหม",
    "ตอนนี้อำเภอบางบาลเป็นยังไงบ้าง",
    "หาดใหญ่ฝนตกหนักไหม",
    "พรุ่งนี้ปากเกร็ดน้ำท่วมไหม",
    "ทำไมอำเภอสองพี่น้องถึงวิกฤต",
    "Is Hat Yai flooding?",
    "What is the flood situation in Sena now?",
    "เชียงใหม่มีไฟป่าไหม",
    "นครศรีธรรมราชฝนตกหนักไหม",
    "อำเภอเมืองกาญจนบุรีน้ำท่วมหรือเปล่า",
    "3 วันข้างหน้าบางปะอินน้ำจะท่วมไหม",
    "กรุงเทพเป็นยังไงบ้าง",
]


class SnapshotTools:
    """Serves decisions from the last live snapshot so the eval does not depend on provider uptime."""

    def __init__(self, path: Path):
        self.rows = json.loads(path.read_text(encoding="utf-8"))

    def get_assessments(self, code: str) -> list[dict]:
        order = {None: -1, "NORMAL": 0, "WATCH": 1, "WARNING": 2, "CRITICAL": 3}
        rows = [r for r in self.rows if r["area_code"].startswith(code[:6] if len(code) >= 6 else code)]
        worst: dict[str, dict] = {}
        for r in rows:
            if r["hazard"] not in worst or order[r["level"]] > order[worst[r["hazard"]]["level"]]:
                worst[r["hazard"]] = {**r, "sources": ["NASA FIRMS"] if r["hazard"] == "fire" else ["ThaiWater (สสน.)"]}
        return list(worst.values())

    def get_official_warnings(self) -> list[dict]:
        return [{"title": "ฝนตกหนักถึงหนักมากบริเวณประเทศไทย (มีผลกระทบในช่วงวันที่ 18-25 ตุลาคม 2569) ฉบับที่ 4 (263/2569)",
                 "hazards": ["rain_storm", "flood"]}]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.WARNING, format="  %(name)s: %(message)s")
    explainer = GeminiExplainer.from_env()
    if explainer is None:
        sys.exit("GEMINI_API_KEY is not set in .env")
    print(f"model {explainer.config.model}, thinking {explainer.config.thinking_level}\n")
    agent = HazardAgent(Gazetteer.load(ROOT / "data/processed/gazetteer.json"),
                        SnapshotTools(ROOT / "data/processed/area_snapshot.json"), explainer=explainer)
    times, used, total = [], 0, 0
    for q in QUESTIONS:
        start = time.perf_counter()
        a = agent.ask(q, datetime.now(timezone.utc))
        elapsed = time.perf_counter() - start
        if a.facts:  # only risk/explain answers go to the LLM
            total += 1
            used += a.llm_used
            times.append(elapsed)
            # Re-check independently so the report does not trust the explainer's own result.
            assert not a.llm_used or validate(a.text, a.facts, q) == []
        print(f"=== {q}  [{elapsed:.1f}s, llm_used={a.llm_used}]\n{a.text}\n")
        time.sleep(PAUSE_SECONDS)
    if times:
        print(f"LLM answers accepted: {used}/{total}  |  median {statistics.median(times):.1f}s, max {max(times):.1f}s")


if __name__ == "__main__":
    main()
