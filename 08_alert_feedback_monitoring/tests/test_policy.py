from datetime import datetime, time, timedelta, timezone

from alerting import AreaDecision, SentState, Subscription, group_by_user, plan_alerts
from alerting.policy import in_quiet_hours

BKK = timezone(timedelta(hours=7))
NOON = datetime(2026, 10, 19, 12, 0, tzinfo=BKK)
NIGHT = datetime(2026, 10, 19, 23, 30, tzinfo=BKK)
SUB = Subscription("u1", ("TH1412",), ("flood", "rain_storm"), min_level="WARNING")


def d(level, hazard="flood", reason="ระดับน้ำสูงกว่าตลิ่ง 1.21 ม."):
    return AreaDecision("TH1412", "อ.เสนา จ.พระนครศรีอยุธยา", hazard, level, reason, NOON)


def test_raise_to_warning_sends():
    alerts = plan_alerts([d("WARNING")], [SUB], SentState(), NOON)
    assert len(alerts) == 1 and alerts[0].kind == "raised"
    assert "อ.เสนา" in alerts[0].message and "เตือนภัย" in alerts[0].message and "1784" in alerts[0].message


def test_below_minimum_level_is_silent():
    assert plan_alerts([d("WATCH")], [SUB], SentState(), NOON) == []


def test_same_level_is_not_repeated():
    state = SentState()
    plan_alerts([d("WARNING")], [SUB], state, NOON)
    assert plan_alerts([d("WARNING")], [SUB], state, NOON + timedelta(hours=5)) == []


def test_lowered_from_warning_sends_notice():
    state = SentState()
    plan_alerts([d("WARNING")], [SUB], state, NOON)
    alerts = plan_alerts([d("NORMAL")], [SUB], state, NOON + timedelta(hours=4))
    assert alerts[0].kind == "lowered" and "ลดระดับ" in alerts[0].message


def test_small_drop_is_silent_but_tracked():
    state = SentState(levels={("u1", "TH1412", "flood"): "WATCH"})
    assert plan_alerts([d("NORMAL")], [SUB], state, NOON) == []
    assert state.levels[("u1", "TH1412", "flood")] == "NORMAL"


def test_quiet_hours_hold_warning_but_not_critical():
    assert plan_alerts([d("WARNING")], [SUB], SentState(), NIGHT) == []
    assert len(plan_alerts([d("CRITICAL")], [SUB], SentState(), NIGHT)) == 1


def test_cooldown_stops_flapping_but_not_critical():
    state = SentState()
    assert len(plan_alerts([d("WARNING")], [SUB], state, NOON)) == 1
    # Water dips just below the threshold: one "lowered" notice.
    assert plan_alerts([d("WATCH")], [SUB], state, NOON + timedelta(minutes=30))[0].kind == "lowered"
    # Back to WARNING 20 minutes later: inside the cooldown, so no third push.
    assert plan_alerts([d("WARNING")], [SUB], state, NOON + timedelta(minutes=50)) == []
    # After the cooldown it is sent again.
    assert len(plan_alerts([d("WARNING")], [SUB], state, NOON + timedelta(hours=4))) == 1
    # CRITICAL always goes through.
    state2 = SentState(levels={("u1", "TH1412", "flood"): "WATCH"}, sent_at={("u1", "TH1412", "flood"): NOON})
    assert len(plan_alerts([d("CRITICAL")], [SUB], state2, NOON + timedelta(minutes=5))) == 1


def test_missing_data_never_alerts():
    assert plan_alerts([d(None)], [SUB], SentState(), NOON) == []


def test_only_subscribed_hazards():
    assert plan_alerts([d("CRITICAL", hazard="fire")], [SUB], SentState(), NOON) == []


def test_group_by_user_puts_worst_first():
    alerts = plan_alerts([d("WARNING", "rain_storm", "ฝน 120 มม."), d("CRITICAL")], [SUB], SentState(), NOON)
    message = group_by_user(alerts)["u1"]
    assert message.index("วิกฤต") < message.index("เตือนภัย")


def test_quiet_hours_across_midnight():
    assert in_quiet_hours(NIGHT, time(22), time(6))
    assert in_quiet_hours(datetime(2026, 10, 19, 5, 0, tzinfo=BKK), time(22), time(6))
    assert not in_quiet_hours(NOON, time(22), time(6))
    assert not in_quiet_hours(NIGHT, None, None)
