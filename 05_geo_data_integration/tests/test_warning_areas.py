import json
from datetime import date
from pathlib import Path

import pytest

from external_data.adapters.tmd import classify_hazards, read_days
from geo_integration import (Gazetteer, hazard_areas, named_provinces, provinces_by_hazard, table_provinces, warning_parts,
                             warning_provinces, warning_statements)
from geo_integration.warning_areas import REGIONS
from thanphai_shared import HazardType

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


def test_issue_12_heavy_rain_reaches_only_the_east_and_south(gaz):
    # "ส่วนภาคเหนือตอนล่าง … ยังคงมีฝนฟ้าคะนอง": the north, northeast, central and Bangkok parts name no hazard of
    # ours, so เชียงใหม่ (TH50) gets neither the heavy-rain nor the flood warning.
    assert warning_parts(text("12"))[1].startswith("ส่วนภาคเหนือตอนล่าง")
    by_hazard = provinces_by_hazard(gaz, text("12"), classify_hazards)
    assert by_hazard == {HazardType.RAIN_STORM: sorted(EAST + SOUTH), HazardType.FLOOD: sorted(EAST + SOUTH)}
    assert all("TH50" not in codes for codes in by_hazard.values())


def test_issue_4_both_parts_have_heavy_rain(gaz):
    by_hazard = provinces_by_hazard(gaz, text("4"), classify_hazards)
    assert by_hazard[HazardType.FLOOD] == by_hazard[HazardType.RAIN_STORM] == warning_provinces(gaz, text("4"))


def test_each_part_keeps_its_own_hazards(gaz):
    # v1 storm news: flash floods for the whole south; "คลื่นลมแรง" (rain_storm) adds the west coast, already in it.
    by_hazard = provinces_by_hazard(gaz, text("1", "v1"), classify_hazards)
    assert by_hazard == {HazardType.RAIN_STORM: sorted(SOUTH), HazardType.FLOOD: sorted(SOUTH)}
    assert provinces_by_hazard(gaz, "พายุ X สำหรับภาคใต้ฝั่งตะวันตกให้ระวังคลื่นลมแรง ส่วนภาคเหนือมีฝนตกหนักถึงหนักมาก",
                               classify_hazards) == {HazardType.RAIN_STORM: sorted(SOUTH_WEST + NORTH), HazardType.FLOOD: NORTH}


def test_live_issue_3_flood_only_in_the_south_rain_everywhere(gaz):
    # Title: unsettled weather in upper Thailand "และ" heavy rain (rain + flood) in the south. Headline: heavy rain
    # in places in upper Thailand, first the northeast, "ส่วนภาคเหนือ … จะได้รับผลกระทบในระยะถัดไป" later.
    issue = next(f for f in FIXTURES if f["issue_no"] == "3")
    assert warning_parts(issue["title"])[1].startswith("และฝนตกหนักถึงหนักมากบริเวณภาคใต้")
    by_hazard = provinces_by_hazard(gaz, [issue["title"], issue["headline"]], classify_hazards)
    assert by_hazard[HazardType.FLOOD] == sorted(SOUTH)
    assert by_hazard[HazardType.RAIN_STORM] == sorted(NORTH + NORTHEAST + CENTRAL + BANGKOK_AREA + EAST + SOUTH)


def test_live_issue_3_upper_thailand_days_5_to_7_south_5_to_9(gaz):
    # Title: south for 5-9 Nov. Headline: "ในช่วงวันที่ 5–7 พฤศจิกายน 2569 บริเวณประเทศไทยตอนบน", and "ส่วนภาคเหนือ …
    # จะได้รับผลกระทบในระยะถัดไป" keeps those days.
    issue = next(f for f in FIXTURES if f["issue_no"] == "3")
    areas = hazard_areas(gaz, [issue["title"], issue["headline"]], classify_hazards, lambda part: read_days(part, date(2026, 11, 2)))
    upper, south = (date(2026, 11, 5), date(2026, 11, 7)), (date(2026, 11, 5), date(2026, 11, 9))
    assert areas[HazardType.RAIN_STORM] == {code: upper for code in NORTH + NORTHEAST + CENTRAL + BANGKOK_AREA + EAST} | {
        code: south for code in SOUTH}
    assert areas[HazardType.FLOOD] == {code: south for code in SOUTH}


def test_province_in_two_statements_gets_both_spans(gaz):
    days = {"18-20": (date(2026, 10, 18), date(2026, 10, 20)), "21-25": (date(2026, 10, 21), date(2026, 10, 25))}

    def read(part):
        return next((d for k, d in days.items() if k in part), None)

    text = "ในช่วงวันที่ 18-20 ภาคใต้มีฝนตกหนัก หลังจากนั้นในช่วงวันที่ 21-25 ภาคใต้และภาคกลางมีฝนตกหนัก ขอให้ภาคกลางระวังฝนตกหนัก"
    areas = hazard_areas(gaz, text, classify_hazards, read)[HazardType.RAIN_STORM]
    assert areas["TH90"] == (date(2026, 10, 18), date(2026, 10, 25))
    assert areas["TH14"] is None  # "ขอให้ …" gives no days: the warning's own days, the widest


def test_same_impact_takes_the_hazards_of_the_statement_before(gaz):
    assert provinces_by_hazard(gaz, "ภาคใต้มีฝนตกหนัก ส่วนภาคตะวันออกจะได้รับผลกระทบในระยะถัดไป", classify_hazards) == {
        HazardType.RAIN_STORM: sorted(SOUTH + EAST)}
    # Without "ผลกระทบ" a statement with no hazard word still sets nothing (issue 12).
    assert provinces_by_hazard(gaz, "ภาคใต้มีฝนตกหนัก ส่วนภาคตะวันออกมีฝนฟ้าคะนอง", classify_hazards) == {
        HazardType.RAIN_STORM: sorted(SOUTH)}


def test_and_with_weather_but_no_area_stays_with_its_area(gaz):
    assert warning_parts("ภาคใต้มีฝนฟ้าคะนอง และมีฝนตกหนักบางแห่ง") == ["ภาคใต้มีฝนฟ้าคะนอง และมีฝนตกหนักบางแห่ง"]
    assert provinces_by_hazard(gaz, "ภาคใต้มีฝนฟ้าคะนอง และมีฝนตกหนักบางแห่ง", classify_hazards) == {HazardType.RAIN_STORM: sorted(SOUTH)}


def test_mostly_is_not_a_new_part():
    assert warning_parts("ฝนตกหนัก ส่วนมากบริเวณจังหวัดตาก") == ["ฝนตกหนัก ส่วนมากบริเวณจังหวัดตาก"]


def test_named_provinces_leave_out_regions(gaz):
    # Issue 288/2569 named regions only; rules.yaml gives those a lower level than a province named by name.
    issue = next(f for f in FIXTURES if f["issue_no"] == "3")
    assert all(not s.named for s in warning_statements(gaz, [issue["title"], issue["headline"]], classify_hazards))
    assert named_provinces(gaz, "ฝนตกหนักบริเวณภาคใต้ โดยเฉพาะจังหวัดสงขลา และกทม.") == ["TH10", "TH90"]
    assert named_provinces(gaz, "ภาคใต้(ฝั่งตะวันตก) และกรุงเทพฯและปริมณฑล") == []


# Statements of the PDF of issue 5 (288/2569), page 1, as external_data.adapters.tmd.bulletin_body reads them.
BULLETIN_5 = json.loads((Path(__file__).with_name("fixtures") / "tmd_bulletin_5.json").read_text(encoding="utf-8"))["body"]


def test_bulletin_gives_each_region_its_days_and_flood_advice_stays_in_the_south(gaz):
    areas = hazard_areas(gaz, BULLETIN_5, classify_hazards, lambda part: read_days(part, date(2026, 11, 3)))
    upper = NORTH + NORTHEAST + CENTRAL + BANGKOK_AREA + EAST
    assert sorted(areas[HazardType.RAIN_STORM]) == sorted(upper + SOUTH)
    assert {areas[HazardType.RAIN_STORM][c] for c in upper} == {(date(2026, 11, 5), date(2026, 11, 7))}
    assert {areas[HazardType.RAIN_STORM][c] for c in SOUTH} == {(date(2026, 11, 7), date(2026, 11, 9))}
    # "ขอให้ประชาชนระวัง … น้ำท่วมฉับพลัน" names no area: it is the south's advice, not a flood warning for the north.
    assert areas[HazardType.FLOOD] == {c: (date(2026, 11, 7), date(2026, 11, 9)) for c in SOUTH}
    south = [s for s in warning_statements(gaz, BULLETIN_5, classify_hazards) if s.regions == ["ภาคใต้"]]
    assert [s.hazards for s in south] == [[HazardType.RAIN_STORM], [HazardType.FLOOD]]
    assert "หนักมาก" in south[1].wording and "น้ำท่วมฉับพลัน" in south[1].wording


def test_table_row_labels_are_not_whole_regions(gaz):
    # Daily table of issue 5 (5 Nov): "ภาคใต้:" heads six southern provinces, not the whole south.
    row = ("ภาคใต้: จังหวัดชุมพร สุราษฎร์ธานี นครศรีธรรมราช สงขลา ปัตตานี และนราธิวาส "
           "ภาคกลาง: จังหวัดนครปฐม รวมทั้งกรุงเทพมหานครและปริมณฑล")
    assert table_provinces(gaz, row) == sorted({"TH86", "TH84", "TH80", "TH90", "TH94", "TH96", "TH73", *BANGKOK_AREA})
    assert table_provinces(gaz, "ภาคตะวันออก: จังหวัดชลบุรี และระยอง") == ["TH20", "TH21"]


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
