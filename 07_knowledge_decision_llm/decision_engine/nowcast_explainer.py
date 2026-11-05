"""Qwen local AI Nowcast Explainer and strict zero-trust validator.

Summarizes rain cell movement, direction, velocity, and impacted districts
within a 1-hour nowcast horizon without changing deterministic risk levels.
"""

from dataclasses import dataclass, field
import json
import os
import re
import urllib.request
from typing import Any, Callable

from .explainer import QwenConfig, call_qwen_api

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
    if not summary.location_desc or summary.direction_th == "ไม่มีกลุ่มฝน":
        return "ไม่พบกลุ่มฝนที่มีนัยสำคัญในระยะคาดการณ์ 1 ชั่วโมงข้างหน้า"

    if summary.direction_th in ["ยังประเมินทิศทางไม่ได้", "ทิศทางไม่แน่นอน"]:
        return f"ตรวจพบกลุ่มฝน{summary.intensity_desc}{summary.location_desc} ยังประเมินทิศทางไม่ได้"

    current = [t.amphoe_th for t in summary.targets if t.eta_min == 0]
    approaching = [t for t in summary.targets if t.eta_min > 0]

    current_part = f" ขณะนี้ฝนตกที่{' และ'.join(current[:2])}" if current else ""
    approaching_items = [f"{t.amphoe_th} ใน {t.eta_min} นาที" for t in approaching[:2]]
    approaching_part = f" คาดว่าจะถึง{' และ'.join(approaching_items)}" if approaching_items else ""

    if summary.speed_kmh < 2.0 or summary.direction_th in ["เกือบอยู่กับที่", "อยู่กับที่"]:
        return (
            f"กลุ่มฝน{summary.intensity_desc}{summary.location_desc} "
            f"มีการเคลื่อนตัวช้าหรือเกือบอยู่กับที่{current_part} "
            f"โปรดเฝ้าระวังฝนสะสมและน้ำท่วมฉับพลัน"
        )

    speed_val = int(round(summary.speed_kmh))
    return (
        f"กลุ่มฝน{summary.intensity_desc}{summary.location_desc} "
        f"กำลังเคลื่อนตัวไปทาง{summary.direction_th} "
        f"ด้วยความเร็วราว {speed_val} กม./ชม.{current_part}{approaching_part}"
    )


def validate_nowcast_explanation(
    answer: str,
    summary: NowcastSummary,
    gazetteer: Any = None,
) -> list[str]:
    """Strict zero-trust validator for nowcast explanation text."""
    problems: list[str] = []

    # 1. No emergency numbers in LLM response (attached by system only)
    for num in EMERGENCY_NUMBERS:
        if num in answer:
            problems.append(f"Contains emergency number: {num}")

    # 2. Extract and verify numbers (speed, ETA)
    # Use r"\d+(?:\.\d+)?" without word boundaries because Thai chars are \w and break \b
    raw_nums = re.findall(r"\d+(?:\.\d+)?", answer)
    found_nums = set(raw_nums)

    allowed_nums = set()
    if summary.speed_kmh >= 2.0:
        allowed_nums.add(str(int(round(summary.speed_kmh))))
        allowed_nums.add(f"{summary.speed_kmh:.1f}")
        allowed_nums.add(str(int(summary.speed_kmh)))
    for t in summary.targets:
        if t.eta_min > 0:
            allowed_nums.add(str(t.eta_min))
        else:
            allowed_nums.add("0")

    unknown_nums = found_nums - allowed_nums
    if unknown_nums:
        problems.append(f"Contains numbers not in facts: {sorted(unknown_nums)}")

    # 3. Verify motion direction
    expected_dir = summary.direction_th
    # Remove location_desc from answer first so phrases like "ทางตะวันตกของกรุงเทพฯ" do not trigger false direction mismatch
    text_for_dir_check = answer.replace(summary.location_desc, "")
    if expected_dir in ["ยังประเมินทิศทางไม่ได้", "ทิศทางไม่แน่นอน", "ไม่มีกลุ่มฝน"]:
        for d in ALL_DIRECTIONS:
            if d in text_for_dir_check:
                problems.append(f"Contains direction mismatch: found {d}, expected unknown direction")
                break
    elif expected_dir in ["เกือบอยู่กับที่", "อยู่กับที่"]:
        for d in ALL_DIRECTIONS:
            if d in text_for_dir_check:
                problems.append(f"Contains direction mismatch: found {d}, expected stationary")
                break
    else:
        for d in ALL_DIRECTIONS:
            if d in text_for_dir_check and d != expected_dir:
                if d not in expected_dir:
                    problems.append(f"Contains direction mismatch: found {d}, expected {expected_dir}")
                    break

    # 4. Verify target districts mentioned against facts
    allowed_districts = {t.amphoe_th for t in summary.targets}
    if gazetteer is not None and hasattr(gazetteer, "areas"):
        # Check against all known amphoe names in the country
        for a in gazetteer.areas:
            if a.level == "amphoe" and len(a.name_th) >= 3:
                if a.name_th in answer and a.name_th not in allowed_districts and a.name_th not in summary.location_desc:
                    problems.append(f"Contains unconfirmed district: {a.name_th}")
    else:
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

    def __init__(
        self,
        requester: Callable[[str], str] | None = None,
        config: QwenConfig | None = None,
    ):
        self.requester = requester
        self.config = config or QwenConfig.from_env()

    def _call_qwen(self, prompt: str) -> str:
        if self.requester:
            return self.requester(prompt)

        return call_qwen_api(
            endpoint=self.config.endpoint,
            model=self.config.model,
            system_instruction=self.SYSTEM_PROMPT,
            user_prompt=prompt,
            temperature=self.config.temperature,
            max_tokens=256,
            timeout_ms=self.config.timeout_ms,
        )

    def explain_with_source(self, summary: NowcastSummary, gazetteer: Any = None) -> tuple[str, str]:
        """Return the safe explanation and whether it came from Qwen or the template."""
        template = build_template_answer(summary)
        if not summary.location_desc or summary.direction_th in ["ไม่มีกลุ่มฝน", "ยังประเมินทิศทางไม่ได้", "ทิศทางไม่แน่นอน"]:
            return template, "template"

        # Check if Qwen is explicitly enabled via LLM_PROVIDER=qwen
        # If not enabled or requester not injected, return safe template directly
        provider = os.getenv("LLM_PROVIDER", "").lower()
        if provider != "qwen" and self.requester is None:
            return template, "template"

        targets_str_parts = []
        current = [t.amphoe_th for t in summary.targets if t.eta_min == 0]
        if current:
            targets_str_parts.append(f"ขณะนี้ฝนตกที่: {', '.join(current)}")
        approaching = [f"{t.amphoe_th} ในอีก {t.eta_min} นาที" for t in summary.targets if t.eta_min > 0]
        if approaching:
            targets_str_parts.append(f"คาดว่าจะถึง: {', '.join(approaching)}")
        targets_str = "; ".join(targets_str_parts) or "ไม่มีอำเภอเสี่ยงโดยตรง"
        speed_str = (
            f"ความเร็ว: {int(round(summary.speed_kmh))} กม./ชม.\n"
            if summary.speed_kmh >= 2.0
            else "ความเร็ว: น้อยกว่า 2 กม./ชม. (เกือบอยู่กับที่)\n"
        )
        prompt = (
            f"ตำแหน่ง: {summary.location_desc}\n"
            f"ทิศทาง: {summary.direction_th}\n"
            f"{speed_str}"
            f"ความแรง: {summary.intensity_desc}\n"
            f"อำเภอเป้าหมาย: {targets_str}\n\n"
            f"จงสรุปสถานการณ์สั้นๆ 1-2 ประโยค:"
        )

        try:
            raw_answer = self._call_qwen(prompt)
            problems = validate_nowcast_explanation(raw_answer, summary, gazetteer=gazetteer)
            if not problems:
                return raw_answer, "qwen"
        except Exception:
            pass

        return template, "template"

    def explain(self, summary: NowcastSummary, gazetteer: Any = None) -> str:
        """Explain nowcast summary using Qwen; falls back to template on any error or validation failure."""
        return self.explain_with_source(summary, gazetteer)[0]
