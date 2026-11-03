"""Qwen local AI Nowcast Explainer and strict zero-trust validator.

Summarizes rain cell movement, direction, velocity, and impacted districts
within a 1-hour nowcast horizon without changing deterministic risk levels.
"""

from dataclasses import dataclass, field
import os
import re
from typing import Callable

from .explainer import QwenConfig, QwenExplainer

EMERGENCY_NUMBERS = ["1784", "1669", "199", "191", "1193", "1195", "1197", "1584"]

ALL_DIRECTIONS = [
    "ตะวันออกเฉียงเหนือ",
    "ตะวันออกเฉียงใต้",
    "ตะวันตกเฉียงเหนือ",
    "ตะวันตกเฉียงใต้",
    "เหนือ",
    "ใต้",
    "ตะวันออก",
    "ตะวันตก",
]

# Common locations to guard against hallucinated places
COMMON_PLACES = [
    "พัทยา", "หัวหิน", "หาดใหญ่", "เชียงใหม่", "ภูเก็ต", "ขอนแก่น", "โคราช", "อยุธยา",
    "บางนา", "พระโขนง", "ดอนเมือง", "จตุจักร", "บางกะปิ", "คลองเตย", "ลาดกระบัง",
    "เมือง", "ปากเกร็ด", "บางบัวทอง", "บางกรวย", "เสนา", "บางบาล", "สองพี่น้อง",
]


@dataclass(frozen=True)
class RainCellTarget:
    amphoe_th: str
    eta_min: int


@dataclass(frozen=True)
class NowcastSummary:
    location_desc: str
    direction_th: str
    speed_kmh: float
    intensity_desc: str = "ปานกลาง"
    targets: list[RainCellTarget] = field(default_factory=list)


def build_template_answer(summary: NowcastSummary) -> str:
    """Generate a safe, deterministic template sentence without LLM."""
    if summary.speed_kmh < 2.0 or not summary.location_desc or summary.direction_th in ["ไม่มีกลุ่มฝน", "ทิศทางไม่แน่นอน"]:
        return "ตรวจไม่พบกลุ่มฝนที่มีนัยสำคัญในระยะคาดการณ์ 1 ชั่วโมงข้างหน้า"

    speed_val = int(round(summary.speed_kmh))
    targets_part = ""
    if summary.targets:
        items = [f"{t.amphoe_th} ใน {t.eta_min} นาที" for t in summary.targets[:3]]
        targets_part = " คาดว่าจะถึง" + " และ".join(items)

    return (
        f"กลุ่มฝน{summary.intensity_desc}{summary.location_desc} "
        f"กำลังเคลื่อนตัวไปทาง{summary.direction_th} "
        f"ด้วยความเร็วราว {speed_val} กม./ชม.{targets_part}"
    )


def validate_nowcast_explanation(answer: str, summary: NowcastSummary) -> list[str]:
    """Strict zero-trust validator for nowcast explanation text."""
    problems: list[str] = []

    # 1. No emergency numbers in LLM response (attached by system only)
    for num in EMERGENCY_NUMBERS:
        if num in answer:
            problems.append(f"Contains emergency number: {num}")

    # 2. Extract and verify numbers (speed, ETA)
    found_nums = set(re.findall(r"\b\d+(?:\.\d+)?\b", answer))
    allowed_nums = set()
    if summary.speed_kmh > 0:
        allowed_nums.add(str(int(round(summary.speed_kmh))))
        allowed_nums.add(f"{summary.speed_kmh:.1f}")
        allowed_nums.add(str(int(summary.speed_kmh)))
    for t in summary.targets:
        allowed_nums.add(str(t.eta_min))

    # Also allow standard time window bounds if mentioned (e.g. 1 ชม., 60 นาที)
    allowed_nums.update(["1", "60"])

    unknown_nums = found_nums - allowed_nums
    if unknown_nums:
        problems.append(f"Contains numbers not in facts: {sorted(unknown_nums)}")

    # 3. Verify motion direction
    expected_dir = summary.direction_th
    # Remove location_desc from answer first so phrases like "ทางตะวันตกของกรุงเทพฯ" do not trigger false direction mismatch
    text_for_dir_check = answer.replace(summary.location_desc, "")
    for d in ALL_DIRECTIONS:
        if d in text_for_dir_check and d != expected_dir:
            if d not in expected_dir:
                problems.append(f"Contains direction mismatch: found {d}, expected {expected_dir}")
                break

    # 4. Verify target districts mentioned
    allowed_districts = {t.amphoe_th for t in summary.targets}
    # Check if answer contains locations not in allowed districts or summary location
    for place in COMMON_PLACES:
        if place in answer and place not in allowed_districts and place not in summary.location_desc:
            problems.append(f"Contains unconfirmed district: {place}")

    return problems


class NowcastExplainer:
    """Wraps Qwen model to explain radar nowcasting with safe fallback."""

    SYSTEM_PROMPT = """คุณคือผู้ช่วยรายงานทิศทางฝนของระบบทันภัย
หน้าที่: สรุปการเคลื่อนที่ของกลุ่มฝนสั้นๆ 1-2 ประโยคจากข้อมูลที่กำหนดให้เท่านั้น
กฎเหล็ก:
1. ห้ามแต่งตัวเลขความเร็ว หรือเวลาคาดการณ์ (นาที) ขึ้นมาเองเด็ดขาด ใช้เฉพาะตัวเลขที่กำหนด
2. ห้ามเปลี่ยนชื่ออำเภอ หรือทิศทางการเคลื่อนที่
3. ห้ามใส่เบอร์โทรฉุกเฉิน (ระบบแนบให้เอง)
4. หากไม่มีกลุ่มฝน ให้ตอบสั้นๆ ว่าไม่พบกลุ่มฝนที่มีนัยสำคัญ"""

    def __init__(self, requester: Callable[[str], str] | None = None, config: QwenConfig | None = None):
        self.requester = requester
        self.config = config or QwenConfig()

    def _call_qwen(self, prompt: str) -> str:
        if self.requester:
            return self.requester(prompt)
        import json
        import urllib.request

        url = f"{self.config.endpoint.rstrip('/')}/api/generate"
        payload = json.dumps({
            "model": self.config.model,
            "prompt": prompt,
            "system": self.SYSTEM_PROMPT,
            "stream": False,
            "options": {"temperature": self.config.temperature},
        }).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        timeout_s = max(1.0, self.config.timeout_ms / 1000.0)
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("response", "").strip()

    def explain(self, summary: NowcastSummary) -> str:
        """Explain nowcast summary using Qwen; falls back to template on any error or validation failure."""
        template = build_template_answer(summary)
        if summary.speed_kmh < 2.0 or not summary.location_desc:
            return template

        # Format prompt
        targets_str = ", ".join(f"{t.amphoe_th} ในอีก {t.eta_min} นาที" for t in summary.targets) or "ไม่มีอำเภอเสี่ยงโดยตรง"
        prompt = (
            f"ตำแหน่ง: {summary.location_desc}\n"
            f"ทิศทาง: {summary.direction_th}\n"
            f"ความเร็ว: {int(round(summary.speed_kmh))} กม./ชม.\n"
            f"ความแรง: {summary.intensity_desc}\n"
            f"อำเภอเป้าหมาย: {targets_str}\n\n"
            f"จงสรุปสถานการณ์สั้นๆ 1-2 ประโยค:"
        )

        try:
            raw_answer = self._call_qwen(prompt)
            problems = validate_nowcast_explanation(raw_answer, summary)
            if not problems:
                return raw_answer
        except Exception:
            pass

        return template
