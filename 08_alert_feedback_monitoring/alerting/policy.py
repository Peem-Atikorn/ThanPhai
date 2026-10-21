"""Decide which alerts to send, following 08_alert_feedback_monitoring/02_step.txt and 03_process.txt.

Pure logic: no network, no database. Delivery (LINE, Web Push, email) happens elsewhere.
"""

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

LEVELS = {"NORMAL": 0, "WATCH": 1, "WARNING": 2, "CRITICAL": 3}
LEVEL_TH = {"NORMAL": "ปกติ", "WATCH": "เฝ้าระวัง", "WARNING": "เตือนภัย", "CRITICAL": "วิกฤต"}
HAZARD_TH = {"flood": "น้ำท่วม", "rain_storm": "ฝนหนัก/พายุ", "drought": "ภัยแล้ง", "fire": "ไฟป่า/ไฟหญ้า", "air_quality": "ฝุ่น PM2.5", "heat": "ความร้อนจัด"}


@dataclass(frozen=True)
class Subscription:
    user_id: str
    area_codes: tuple[str, ...]
    hazards: tuple[str, ...]
    min_level: str = "WARNING"
    channels: tuple[str, ...] = ("line",)
    quiet_start: time | None = time(22, 0)
    quiet_end: time | None = time(6, 0)


@dataclass(frozen=True)
class AreaDecision:
    area_code: str
    area_label: str
    hazard: str
    level: str | None
    reason: str = ""
    data_time: datetime | None = None


@dataclass
class SentState:
    """Last level sent per (user, area, hazard) and when."""

    levels: dict[tuple[str, str, str], str] = field(default_factory=dict)
    sent_at: dict[tuple[str, str, str], datetime] = field(default_factory=dict)


@dataclass(frozen=True)
class Alert:
    user_id: str
    area_code: str
    hazard: str
    level: str
    previous: str | None
    kind: str  # raised | lowered
    channels: tuple[str, ...]
    message: str


def in_quiet_hours(now: datetime, start: time | None, end: time | None) -> bool:
    if start is None or end is None:
        return False
    t = now.timetz().replace(tzinfo=None)
    return start <= t or t < end if start > end else start <= t < end


def build_message(d: AreaDecision, kind: str, previous: str | None) -> str:
    head = "🔴" if d.level == "CRITICAL" else "🟠" if d.level == "WARNING" else "🟡" if d.level == "WATCH" else "🟢"
    if kind == "lowered":
        text = f"{head} {d.area_label}: {HAZARD_TH[d.hazard]} ลดระดับเป็น {LEVEL_TH[d.level]} (จาก {LEVEL_TH[previous]})"
    else:
        text = f"{head} {d.area_label}: {HAZARD_TH[d.hazard]} ระดับ {LEVEL_TH[d.level]}"
    if d.reason:
        text += f"\nเหตุผล: {d.reason}"
    if d.data_time:
        text += f"\nข้อมูล ณ {d.data_time:%d/%m %H:%M} น."
    if d.level in ("WARNING", "CRITICAL"):
        text += "\nฉุกเฉินโทร 1784 (ปภ.) · 1669"
    return text


def plan_alerts(
    decisions: list[AreaDecision],
    subscriptions: list[Subscription],
    state: SentState,
    now: datetime,
    cooldown: timedelta = timedelta(hours=3),
) -> list[Alert]:
    """Return the alerts to send now, and update `state` for those alerts.

    Rules: send when the level rises to or above the user's minimum; send a "lowered" notice only when
    coming down from WARNING or CRITICAL; respect cooldown and quiet hours except for CRITICAL;
    never alert on missing data (level None).
    """
    by_key = {(d.area_code, d.hazard): d for d in decisions}
    alerts = []
    for sub in subscriptions:
        quiet = in_quiet_hours(now, sub.quiet_start, sub.quiet_end)
        for area_code in sub.area_codes:
            for hazard in sub.hazards:
                d = by_key.get((area_code, hazard))
                if d is None or d.level is None:
                    continue
                key = (sub.user_id, area_code, hazard)
                previous = state.levels.get(key)
                new, old = LEVELS[d.level], LEVELS.get(previous, 0)
                if new > old and new >= LEVELS[sub.min_level]:
                    kind = "raised"
                elif new < old and old >= LEVELS["WARNING"]:
                    kind = "lowered"
                else:
                    if new < old:
                        state.levels[key] = d.level  # quietly follow small drops (WATCH -> NORMAL)
                    continue
                urgent = d.level == "CRITICAL"
                last = state.sent_at.get(key)
                if not urgent and (quiet or (last and now - last < cooldown and kind == "raised" and old > 0)):
                    continue
                alerts.append(Alert(sub.user_id, area_code, hazard, d.level, previous, kind, sub.channels,
                                    build_message(d, kind, previous)))
                state.levels[key] = d.level
                state.sent_at[key] = now
    return alerts


def group_by_user(alerts: list[Alert]) -> dict[str, str]:
    """One message per user per run, so several hazards do not become several pushes (LINE quota)."""
    grouped: dict[str, list[str]] = {}
    for alert in sorted(alerts, key=lambda a: -LEVELS[a.level]):
        grouped.setdefault(alert.user_id, []).append(alert.message)
    return {user: "\n\n".join(messages) for user, messages in grouped.items()}
