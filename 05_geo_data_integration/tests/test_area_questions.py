import json
from pathlib import Path

import pytest

from geo_integration import Area, Gazetteer
from geo_integration.regions import REGION_CODES


@pytest.fixture(scope="module")
def gazetteer():
    # Synthetic positions and child codes; real names deliberately reproduce substring collisions.
    rows = json.loads((Path(__file__).with_name("fixtures") / "question_areas.json").read_text(encoding="utf-8"))
    return Gazetteer([Area(*row, 13.9, 100.4, 100) for row in rows])


@pytest.mark.parametrize(("question", "codes"), [
    ("ภาคเหนือมีไฟป่ากี่จุด", {"TH50", "TH57"}),
    ("สรุปสถานการณ์ภัยทั้งประเทศวันนี้", set()),
    ("อำเภอเมืองอันตรายไหม", set()),
    ("เขื่อนภูมิพลเหลือน้ำกี่เปอร์เซ็นต์", set()),
    ("เขื่อนสิริกิติ์ปล่อยน้ำเท่าไหร่", set()),
])
def test_issue_examples_resolve_without_false_tambons(gazetteer, question, codes):
    assert {a.code for a in gazetteer.find_in_text(question)} == codes


@pytest.mark.parametrize(("question", "codes"), [
    ("ภาคอีสานฝนตกไหม", {"TH38", "TH40"}),
    ("ภาคตะวันออกเฉียงเหนือฝนตกไหม", {"TH38", "TH40"}),
    ("ภาคตะวันออกฝนตกไหม", {"TH20"}),
    ("ภาคตะวันตกฝนตกไหม", {"TH63"}),
    ("ภาคกลางน้ำท่วมไหม", {"TH12", "TH14", "TH26"}),
    ("ภาคใต้มีคลื่นลมแรงไหม", {"TH80"}),
    ("ภาคเหนือและภาคใต้ฝนตกไหม", {"TH50", "TH57", "TH80"}),
    ("ภาคเหนือ จังหวัดเชียงใหม่มีไฟป่าไหม", {"TH50"}),
])
def test_geographic_regions_and_explicit_province(gazetteer, question, codes):
    assert {a.code for a in gazetteer.find_in_text(question)} == codes


def test_regions_cover_all_provinces_once():
    codes = [code for region in REGION_CODES.values() for code in region]
    assert len(codes) == len(set(codes)) == 77
    all_provinces = Gazetteer([Area(code, "province", code, code, None, 0, 0, 1) for code in codes])
    assert {a.code for a in all_provinces.find_in_text("ภาคเหนือมีไฟป่าไหม")} == {
        "TH50", "TH51", "TH52", "TH53", "TH54", "TH55", "TH56", "TH57", "TH58",
    }


@pytest.mark.parametrize(("question", "codes"), [
    ("นาน้ำท่วมไหม", set()),
    ("ต.นาน้ำท่วมไหม", {"TH120401"}),
    ("นา จังหวัดนนทบุรี ฝนตกไหม", {"TH120401", "TH12"}),
    ("นา อำเภอบางบัวทอง ฝนตกไหม", {"TH120401", "TH1204"}),
    ("เหนือมีไฟป่าไหม", set()),
    ("เหนือ จังหวัดเชียงใหม่มีไฟป่าไหม", {"TH500101", "TH50"}),
    ("อำเภอเหนือมีไฟป่าไหม", set()),
    ("โสนลอยฝนตกไหม", {"TH120402"}),
    ("ฝนตกที่เสนาไหม", {"TH1412"}),
    ("ตากมีไฟป่าไหม", {"TH63"}),
    ("อำเภอเมือง จังหวัดเชียงใหม่ฝนตกไหม", {"TH5001", "TH50"}),
    ("เชียงใหม่ อำเภอเมืองฝนตกไหม", {"TH5001", "TH50"}),
    ("อำเภอเมืองเชียงใหม่ฝนตกไหม", {"TH5001"}),
])
def test_local_name_context(gazetteer, question, codes):
    assert {a.code for a in gazetteer.find_in_text(question)} == codes
