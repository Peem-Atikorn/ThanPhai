import json
from pathlib import Path

import pytest

from geo_integration import Gazetteer, warning_provinces
from geo_integration.warning_areas import REGIONS

REAL = Path(__file__).resolve().parents[2] / "data" / "processed" / "gazetteer.json"
FIXTURES = json.loads((Path(__file__).with_name("fixtures") / "tmd_warning_texts.json").read_text(encoding="utf-8"))
pytestmark = pytest.mark.skipif(not REAL.exists(), reason="run scripts/build_admin_areas.py first")

NORTH = ["TH50", "TH51", "TH52", "TH53", "TH54", "TH55", "TH56", "TH57", "TH58", "TH60", "TH61", "TH62", "TH63",
         "TH64", "TH65", "TH66", "TH67"]
NORTHEAST = ["TH30", "TH31", "TH32", "TH33", "TH34", "TH35", "TH36", "TH37", "TH38", "TH39", "TH40", "TH41", "TH42",
             "TH43", "TH44", "TH45", "TH46", "TH47", "TH48", "TH49"]
CENTRAL = ["TH14", "TH15", "TH16", "TH17", "TH18", "TH19", "TH70", "TH71", "TH72", "TH75"]
BANGKOK_AREA = ["TH10", "TH11", "TH12", "TH13", "TH73", "TH74"]
EAST = ["TH20", "TH21", "TH22", "TH23", "TH24", "TH25", "TH26", "TH27"]
SOUTH_EAST = ["TH76", "TH77", "TH80", "TH84", "TH86", "TH90", "TH93", "TH94", "TH95", "TH96"]
SOUTH_WEST = ["TH81", "TH82", "TH83", "TH85", "TH91", "TH92"]
SOUTH = SOUTH_EAST + SOUTH_WEST


@pytest.fixture(scope="module")
def gaz():
    return Gazetteer.load(REAL)


def text(issue_no: str | None, source: str = "") -> str:
    return next(f["text"] for f in FIXTURES if f["issue_no"] == issue_no and source in f["source"])


def test_regions_cover_every_province_once(gaz):
    provinces = {a.name_th for a in gaz.areas if a.level == "province"}
    regions = ("ภาคเหนือ", "ภาคตะวันออกเฉียงเหนือ", "ภาคกลาง", "กรุงเทพมหานครและปริมณฑล", "ภาคตะวันออก",
               "ภาคใต้ฝั่งตะวันออก", "ภาคใต้ฝั่งตะวันตก")
    names = [name for region in regions for name in REGIONS[region]]
    assert len(names) == len(set(names)) == 77
    assert set(names) == provinces


def test_real_warning_issue_4(gaz):
    # Days 18-20: south and east; days 21-25: lower northeast, central, Bangkok area, east, upper south.
    assert warning_provinces(gaz, text("4")) == sorted(SOUTH + EAST + NORTHEAST + CENTRAL + BANGKOK_AREA)


def test_real_warning_issue_12(gaz):
    # "ภาคเหนือตอนล่าง" counts as the whole north: sub-regions have no fixed province list.
    assert warning_provinces(gaz, text("12")) == sorted(EAST + SOUTH + NORTH + NORTHEAST + CENTRAL + BANGKOK_AREA)


def test_real_v1_storm_news_names_the_south_only(gaz):
    # Seas ("ทะเลอันดามัน", "อ่าวไทย") and other countries are not provinces.
    assert warning_provinces(gaz, text("1", "v1")) == sorted(SOUTH)


def test_province_list_with_short_names(gaz):
    # Only the first name has "จังหวัด"; "ตาก" is three letters and still counts as a list item.
    assert warning_provinces(gaz, text(None, "ภาคเหนือ")) == ["TH58", "TH62", "TH63", "TH65", "TH66", "TH67"]
    assert warning_provinces(gaz, text(None, "ภาคตะวันออก")) == ["TH20", "TH21", "TH22", "TH23", "TH25", "TH26"]


@pytest.mark.parametrize(
    ("warning", "expected"),
    [
        ("ฝนตกหนักบริเวณจ.เชียงใหม่ และ จ.ลำพูน", ["TH50", "TH51"]),
        ("จังหวัดน่าน แพร่ และเลย", ["TH42", "TH54", "TH55"]),
        ("ภาคใต้(ฝั่งตะวันตก) คลื่นสูง 2-3 เมตร", SOUTH_WEST),
        ("ภาคใต้ ฝั่งตะวันออก", SOUTH_EAST),
        ("กรุงเทพฯและปริมณฑล", BANGKOK_AREA),
        ("ภาคอีสาน", NORTHEAST),
        ("ฝนตกหนักในกทม. และโคราช", ["TH10", "TH30"]),
    ],
)
def test_prefixes_aliases_and_spellings(gaz, warning, expected):
    assert warning_provinces(gaz, warning) == sorted(expected)


@pytest.mark.parametrize(
    "warning",
    [
        "ฝนตกหนักถึงหนักมากบริเวณประเทศไทย",
        "ไม่มีฝนเลย",  # "เลย" = at all
        "กรมอุตุนิยมวิทยาเผยแพร่ประกาศ",  # "แพร่" inside "เผยแพร่"
        "ระวังน่านน้ำ",  # "น่าน" inside "น่านน้ำ"
        "อ่าวไทยตอนบน และทะเลอันดามัน",
    ],
)
def test_words_that_are_not_provinces(gaz, warning):
    assert warning_provinces(gaz, warning) == []
