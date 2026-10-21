"""LLM explanation (Gemini). The LLM rewrites facts that the rules already decided; it never decides.

Every LLM answer is checked against the facts. If it adds an alert level, drops one, or contains a
number that is not in the facts, the caller gets None and uses the template answer instead.
"""

import logging
import os
import re
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

log = logging.getLogger("explainer")

# Thai level words as the rules print them, and the exact English words the LLM must use.
LEVELS_TH = ("ปกติ", "เฝ้าระวัง", "เตือนภัย", "วิกฤต")
LEVELS_EN = {"ปกติ": "NORMAL", "เฝ้าระวัง": "WATCH", "เตือนภัย": "WARNING", "วิกฤต": "CRITICAL"}
# Words that mark a line as talking about one hazard (Thai matched as-is, English lower-cased).
FACT_LINE = re.compile(r"^• (น้ำท่วม|ฝนหนัก/พายุ|ไฟป่า/ไฟหญ้า|ภัยแล้ง|ฝุ่น PM2\.5|ความร้อนจัด|แผ่นดินไหว|คลื่นลมแรง): (ปกติ|เฝ้าระวัง|เตือนภัย|วิกฤต)", re.M)
QUESTION_TIME_NUMBER = re.compile(r"(\d+)\s*(?:วัน|ชม|ชั่วโมง|สัปดาห์|อาทิตย์|day|hour|week)", re.I)
# Advice the LLM must not invent ("go to the temple", "you should evacuate").
ADVICE_WORDS = ("อพยพ", "ควร", "ไปที่", "หลบไป", "evacuat", "should", "go to", "move to")
FACT_HAZARD = {"น้ำท่วม": "flood", "ฝนหนัก/พายุ": "rain_storm", "ไฟป่า/ไฟหญ้า": "fire", "ภัยแล้ง": "drought", "ฝุ่น PM2.5": "air_quality", "ความร้อนจัด": "heat", "แผ่นดินไหว": "earthquake", "คลื่นลมแรง": "marine"}
# Forecast days, one line each: "• พยากรณ์ฝน 20/10: ปกติ — 0.7 มม."
FORECAST_LINE = re.compile(r"^• พยากรณ์ฝน \S+: (ปกติ|เฝ้าระวัง|เตือนภัย|วิกฤต)", re.M)

SYSTEM_INSTRUCTION = """คุณคือผู้ช่วยของระบบเตือนภัย "ทันภัย" หน้าที่เดียวของคุณคือเรียบเรียงข้อเท็จจริงที่ได้รับให้อ่านง่าย
กฎ:
1. ใช้เฉพาะข้อเท็จจริงที่ให้มา ห้ามเพิ่มตัวเลข สถานที่ ระดับ หรือการคาดการณ์ใดๆ
2. ระดับเตือนภัยของแต่ละภัยต้องตรงกับที่ให้มา ห้ามเปลี่ยน ห้ามลด ห้ามเพิ่ม เขียนชื่อภัยตามด้วยระดับ และคงค่าที่วัดได้ของภัยนั้นไว้ (เช่น ระดับน้ำ ปริมาณฝน)
2.1 ประกาศของกรมอุตุนิยมวิทยาให้เรียกว่า "ประกาศ" ห้ามเรียกว่าพยากรณ์ของทันภัย และคงเลขที่ประกาศไว้ตรงตัว (เช่น 264/2569 ห้ามแปลงปี)
3. ถ้าข้อเท็จจริงบอกว่ายังไม่มีพยากรณ์ ต้องบอกผู้ใช้ตรงๆ
4. ตอบสั้น ไม่เกิน 4 บรรทัด ภาษาง่าย เขียนเฉพาะสรุปสถานการณ์ ระดับ และค่าที่วัดได้ ไม่ต้องใส่ประกาศ เบอร์ฉุกเฉิน หรือแหล่งที่มา ระบบจะแนบต่อท้ายให้เอง
4.1 ห้ามให้คำแนะนำ เช่น ให้อพยพ ควรทำอะไร หรือไปที่ใด ถ้าไม่มีในข้อเท็จจริง
5. ถ้าคำถามขอสิ่งที่ข้อเท็จจริงไม่มี ให้บอกว่าไม่มีข้อมูล
6. ตอบเป็นภาษาเดียวกับคำถาม ถ้าตอบภาษาอังกฤษ ให้เขียนระดับเป็นตัวพิมพ์ใหญ่ตรงตัว: NORMAL, WATCH, WARNING, CRITICAL
   และเขียนว่า "no forecast" เมื่อข้อเท็จจริงบอกว่ายังไม่มีพยากรณ์"""


@dataclass
class ExplainerConfig:
    model: str = "gemini-3.5-flash-lite"
    thinking_level: str = "LOW"
    max_output_tokens: int = 1024
    timeout_ms: int = 15000

    @classmethod
    def from_env(cls) -> "ExplainerConfig":
        return cls(model=os.getenv("LLM_MODEL", cls.model), thinking_level=os.getenv("LLM_THINKING_LEVEL", cls.thinking_level))


def _numbers(text: str) -> set[str]:
    # Normalise "2.10" and "2.1" to the same token.
    return {n.rstrip("0").rstrip(".") if "." in n else n for n in re.findall(r"\d+(?:\.\d+)?", text)}


HAZARD_WORDS = {
    "flood": ("น้ำท่วม", "flood"),
    "rain_storm": ("ฝนหนัก", "พายุ", "ฝน", "heavy rain", "rain", "storm"),
    "fire": ("ไฟป่า", "fire"),
    "drought": ("ภัยแล้ง", "drought"),
    "air_quality": ("ฝุ่น", "pm2.5", "air quality"),
    "heat": ("ความร้อนจัด", "ดัชนีความร้อน", "heat index", "heat"),
    "earthquake": ("แผ่นดินไหว", "earthquake"),
    "marine": ("คลื่นลมแรง", "คลื่นสูง", "คลื่น", "wave"),
    # Longest keyword wins, so "พยากรณ์ฝน" is a forecast mention, not a rain one.
    "rain_forecast": ("พยากรณ์ฝน", "พยากรณ์", "rain forecast", "forecast"),
}
# Text between two hazard names that still means "same level for both" ("ฝนหนัก/พายุ และไฟป่า: ปกติ").
JOIN_MAX_CHARS = 8


def _mentions(line: str) -> list[tuple[int, int, str]]:
    """Hazard names in a line, longest keyword first, without overlaps, in reading order."""
    lowered = line.lower()
    keywords = sorted(((w, h) for h, words in HAZARD_WORDS.items() for w in words), key=lambda x: -len(x[0]))
    taken: list[tuple[int, int, str]] = []
    for word, hazard in keywords:
        # English: start of a word, any ending ("flooding", "fires", "rainfall"). Thai has no spaces.
        pattern = rf"(?<![a-z]){re.escape(word)}[a-z]*" if word.isascii() else re.escape(word)
        for m in re.finditer(pattern, lowered):
            if all(m.end() <= s or m.start() >= e for s, e, _ in taken):
                taken.append((m.start(), m.end(), hazard))
    return sorted(taken)


def _levels_in(text: str) -> set[str]:
    """Thai level words, plus English level words written in capitals as the prompt requires."""
    found = {w for w in LEVELS_TH if w in text}
    found |= {th for th, en in LEVELS_EN.items() if re.search(rf"\b{en}\b", text)}
    return found


def _hazard_levels(line: str) -> dict[str, set[str]]:
    """Attach each level word to the hazard named just before it in the line."""
    result: dict[str, set[str]] = {}
    mentions = _mentions(line)
    pending: list[str] = []
    for i, (_, end, hazard) in enumerate(mentions):
        segment = line[end : mentions[i + 1][0] if i + 1 < len(mentions) else len(line)]
        levels = _levels_in(segment)
        if levels:
            for h in [*pending, hazard]:
                result.setdefault(h, set()).update(levels)
            pending = []
        elif len(segment.strip()) <= JOIN_MAX_CHARS:
            pending.append(hazard)
        else:
            result.setdefault(hazard, set())
            pending = []
    for h in pending:
        result.setdefault(h, set())
    return result


def validate(answer: str, facts: str, question: str = "") -> list[str]:
    """Return the reasons the answer is unsafe; empty means it can be shown.

    Each level word is attached to the hazard named just before it, so several hazards can share a line,
    and a level word with no hazard before it (a general phrase such as "ระดับเตือนภัยตัดสินด้วยกฎ") is ignored.
    Every hazard in the facts must keep its level, and no hazard may gain another level.
    Numbers may come from the facts or from the user's own question ("3 วันข้างหน้า").
    """
    problems = []
    if not answer.strip():
        return ["empty answer"]
    # From the question, only numbers that set a time window ("3 วันข้างหน้า"). Any other number the
    # user typed ("น้ำสูง 3 เมตรแล้วใช่ไหม") must not come back as if it were measured.
    allowed = _numbers(facts) | {m.group(1) for m in QUESTION_TIME_NUMBER.finditer(question)}
    # Buddhist-era years may be written in the Common Era in English answers (2569 -> 2026), and back.
    allowed |= {str(int(n) + d) for n in allowed if n.isdigit() and len(n) == 4 for d in (-543, 543)}
    extra_numbers = _numbers(answer) - allowed
    if extra_numbers:
        problems.append(f"numbers not in facts: {sorted(extra_numbers)}")

    expected: dict[str, set[str]] = {}
    for h, level in FACT_LINE.findall(facts):
        expected.setdefault(FACT_HAZARD[h], set()).add(level)
    for level in FORECAST_LINE.findall(facts):
        expected.setdefault("rain_forecast", set()).add(level)
    seen: dict[str, set[str]] = {}
    for line in answer.splitlines():
        for hazard, levels in _hazard_levels(line).items():
            seen.setdefault(hazard, set()).update(levels)
    for hazard, levels in expected.items():
        missing = levels - seen.get(hazard, set())
        if missing:
            problems.append(f"level for {hazard} missing or changed (expected {sorted(levels)})")
        extra = seen.get(hazard, set()) - levels
        if extra:
            problems.append(f"level added for {hazard}: {sorted(extra)}")

    # Safety content that must survive the rewrite.
    if "ยังไม่มีพยากรณ์" in facts and not ("ไม่มีพยากรณ์" in answer or "no forecast" in answer.lower()):
        problems.append("dropped the no-forecast notice")
    lowered = answer.lower()
    for word in ADVICE_WORDS:
        if word in lowered and word not in facts.lower():
            problems.append(f"advice not in facts: {word}")
    # Warnings, emergency numbers and sources are appended verbatim by the agent, not written by the LLM.
    return problems


class GeminiExplainer:
    # Same question on the same facts gives the same answer until the data refreshes; saves free-tier quota.
    CACHE_SIZE = 256
    # After a 429 (free tier allows only a few requests per minute), stop calling for a while instead of
    # making every user wait for a call that will fail.
    RATE_LIMIT_PAUSE_SECONDS = 60
    MAX_CONCURRENT_CALLS = 4

    def __init__(self, client, config: ExplainerConfig | None = None):
        self.client = client
        self.config = config or ExplainerConfig()
        self._cache: OrderedDict[tuple[str, str], str] = OrderedDict()
        self._paused_until = 0.0
        self._lock = threading.Lock()  # ask() runs in worker threads
        # ask() runs in the shared thread pool; a slow Gemini (up to the timeout) must not take every thread
        # and stall the data refreshes. Beyond this many calls at once, answer with the template instead.
        self._slots = threading.BoundedSemaphore(self.MAX_CONCURRENT_CALLS)

    @classmethod
    def from_env(cls) -> "GeminiExplainer | None":
        """None when GEMINI_API_KEY is not set; the system then answers with templates only."""
        if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
            return None
        from google import genai
        from google.genai import types

        config = ExplainerConfig.from_env()
        client = genai.Client(http_options=types.HttpOptions(timeout=config.timeout_ms))
        return cls(client, config)

    def __call__(self, question: str, facts: str) -> str | None:
        from google.genai import errors, types

        key = (" ".join(question.lower().split()), facts)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            if time.monotonic() < self._paused_until:
                return None
        if not self._slots.acquire(blocking=False):
            log.info("gemini busy (%d calls in flight); template answer", self.MAX_CONCURRENT_CALLS)
            return None
        try:
            response = self.client.models.generate_content(
                model=self.config.model,
                contents=f"คำถามผู้ใช้: {question}\n\nข้อเท็จจริง (ห้ามเปลี่ยน):\n{facts}",
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    max_output_tokens=self.config.max_output_tokens,
                    thinking_config=types.ThinkingConfig(thinking_level=self.config.thinking_level),
                ),
            )
            answer = (response.text or "").strip()
        except errors.ClientError as exc:  # 4xx: bad key, quota, blocked request
            if getattr(exc, "code", None) == 429:
                with self._lock:
                    self._paused_until = time.monotonic() + self.RATE_LIMIT_PAUSE_SECONDS
                log.warning("gemini rate limited; template answers for %ss", self.RATE_LIMIT_PAUSE_SECONDS)
            else:
                log.warning("gemini client error %s: %s", getattr(exc, "code", "?"), exc)
            return None
        except (errors.ServerError, errors.APIError) as exc:  # 5xx or other API failure
            log.warning("gemini unavailable: %s", exc)
            return None
        except Exception as exc:  # timeouts and network errors surface as httpx/OS errors
            log.warning("gemini call failed: %s: %s", type(exc).__name__, exc)
            return None
        finally:
            self._slots.release()
        problems = validate(answer, facts, question)
        if problems:
            log.warning("gemini answer rejected: %s", "; ".join(problems))
            return None
        with self._lock:
            self._cache[key] = answer
            if len(self._cache) > self.CACHE_SIZE:
                self._cache.popitem(last=False)
        return answer
