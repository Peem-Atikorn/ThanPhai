"""Read-only live checks for module 03; source accuracy still requires manual review."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import urllib.request


# intent, question, required tool; the same questions appear in the manual checklist.
CASES = [
    ("earthquake", "แผ่นดินไหวล่าสุดเกิดที่ไหน", "get_earthquakes"),
    ("storms", "ตอนนี้มีพายุอะไรบ้าง", "get_storms"),
    ("waves", "คลื่นสูงสุดวันนี้เท่าไหร่", "get_waves"),
    ("water_quality", "สถานีไหนมีความเค็มสูงสุด", "get_water_quality"),
    ("pm25_stations", "สถานี PM2.5 สูงสุดตอนนี้เท่าไหร่", "get_pm25_stations"),
    ("fire_hotspots", "ภาคเหนือมีจุดความร้อนไฟป่ากี่จุด", "get_fire_hotspots"),
    ("dam_history", "เขื่อนภูมิพลเทียบกับสัปดาห์ก่อนเป็นอย่างไร", "get_dam_history"),
    ("media", "ขอลิงก์ภาพพยากรณ์ฝนล่าสุด", "get_media"),
]


def valid_time(value):
    try:
        return isinstance(value, str) and datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is not None
    except ValueError:
        return False


def check_answer(case, answer):
    errors, review = [], []
    if not isinstance(answer, dict):
        return {"status": "fail", "errors": ["response must be an object"], "review": []}
    intent, _, tool = case
    text = answer.get("text")
    if answer.get("intent") != intent:
        errors.append(f"expected intent {intent}")
    if not isinstance(text, str) or not text.strip():
        errors.append("missing answer text")
    sources = answer.get("sources")
    if not isinstance(sources, list) or not sources or not all(isinstance(source, str) and source.strip() for source in sources):
        errors.append("missing source list")
    if isinstance(text, str) and "ที่มา:" not in text:
        errors.append("missing visible source attribution")
    calls = answer.get("tool_calls", [])
    if not isinstance(calls, list) or not any(isinstance(call, str) and call.startswith(tool + "(") for call in calls):
        errors.append(f"missing tool call {tool}")
    if answer.get("llm_used") is not False:
        errors.append("direct data answer must bypass the LLM")
    if not valid_time(answer.get("generated_at")):
        errors.append("generated_at must include a timezone")
    if not valid_time(answer.get("data_updated_at")):
        review.append("missing/invalid backend refresh time")
    if not isinstance(answer.get("evidence"), list):
        errors.append("evidence must be a list")
    elif not answer["evidence"]:
        review.append("no evidence: verify empty feed, unavailable source or missing history")
    if isinstance(text, str) and ("เวลาไม่ระบุ" in text or not re.search(r"\d{1,2}/\d{1,2}/\d{4}", text)):
        review.append("check observation/forecast dates in the visible answer")
    for field in ("degraded_services", "inputs_pending"):
        if not isinstance(answer.get(field), list):
            errors.append(f"{field} must be a list")
        elif answer[field]:
            review.append(f"{field}: {', '.join(map(str, answer[field]))}")
    return {"status": "fail" if errors else "review" if review else "contract_pass", "errors": errors, "review": review}


def request_json(url, timeout, question=None):
    data = json.dumps({"question": question}).encode("utf-8") if question is not None else None
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def run(base_url, timeout):
    base_url = base_url.rstrip("/")
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "base_url": base_url,
              "status": "blocked", "cases": [],
              "manual_review": "Compare answers with source endpoints using docs/03_LIVE_CHECK.md; contract_pass does not verify source accuracy or freshness."}
    try:
        ready = request_json(base_url + "/ready", timeout)
        if not isinstance(ready, dict) or ready.get("status") != "ready":
            report["error"] = "API is not ready"
            return report
    except (OSError, ValueError) as exc:
        report["error"] = f"API readiness failed: {exc}"
        return report
    for case in CASES:
        row = {"intent": case[0], "question": case[1]}
        try:
            answer = request_json(base_url + "/v1/ask", timeout, case[1])
            row.update(check_answer(case, answer), answer=answer)
        except (OSError, ValueError) as exc:
            row.update(status="fail", errors=[str(exc)], review=[])
        report["cases"].append(row)
    statuses = {row["status"] for row in report["cases"]}
    report["status"] = "fail" if "fail" in statuses else "review" if "review" in statuses else "contract_pass"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8002")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "data/cache/agent_smoke.json")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    report = run(args.url, args.timeout)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{report['status']}: {args.output}")
    for row in report["cases"]:
        print(f"  {row['intent']}: {row['status']}")
    if "error" in report:
        print(report["error"])
    return 1 if report["status"] == "fail" else 2 if report["status"] in {"blocked", "review"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
