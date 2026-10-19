import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from external_data.adapters.base import BANGKOK
from external_data.adapters.thaiwater_dams import ThaiWaterDams, dam_status

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "tw_thailand_main_dams.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 10, 19, tzinfo=timezone.utc)


def test_parse_real_rows():
    records = ThaiWaterDams().parse(FIXTURE, NOW)
    assert len(records) == 3
    top = records[0]
    assert top.extra["name_th"] == "หนองปลาไหล" and top.value == pytest.approx(112.39)
    assert top.extra["status"] == "HIGH" and top.extra["status_th"] == "น้ำเกินระดับกักเก็บปกติ"
    assert top.extra["storage_mcm"] > 0 and top.extra["normal_storage_mcm"] > 0
    assert top.province_code and top.lat and top.lon
    assert top.observed_at.tzinfo == BANGKOK and top.extra["agency_th"] == "ชป."
    assert top.extra["released_acc_mcm"] > 0 and top.extra["inflow_acc_mcm"] > 0


@pytest.mark.parametrize(
    ("storage", "usable", "code", "label"),
    [
        (112.4, 104.0, "HIGH", "น้ำเกินระดับกักเก็บปกติ"),
        (85.0, 80.0, "HIGH", "น้ำใกล้เต็มอ่าง"),
        (60.0, 55.0, "NORMAL", "น้ำพอใช้"),
        (40.0, 35.0, "NORMAL", "น้ำค่อนข้างน้อย"),
        (35.0, 25.0, "LOW_CRITICAL", "น้ำใช้การได้เหลือน้อย"),  # usable water decides "critical", not storage
        (20.0, None, "NORMAL", "น้ำน้อย"),
        (None, None, "UNKNOWN", "ไม่มีข้อมูล"),
    ],
)
def test_dam_status_follows_thaiwater_rule(storage, usable, code, label):
    assert dam_status(storage, usable) == (code, label)


def test_parse_handles_empty_bundle():
    assert ThaiWaterDams().parse({}, NOW) == []
    assert ThaiWaterDams().parse({"dam": {"data": []}}, NOW) == []
