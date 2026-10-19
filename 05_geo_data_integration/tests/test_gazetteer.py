from pathlib import Path

import pytest

from geo_integration import Area, Gazetteer
from geo_integration.gazetteer import normalize, split_prefix

REAL = Path(__file__).resolve().parents[2] / "data" / "processed" / "gazetteer.json"


def area(code, level, th, en, parent=None):
    return Area(code, level, th, en, parent, 13.9, 100.4, 100.0)


SMALL = Gazetteer(
    [
        area("TH12", "province", "นนทบุรี", "Nonthaburi"),
        area("TH1205", "amphoe", "บางบัวทอง", "Bang Bua Thong", "TH12"),
        area("TH120501", "tambon", "โสนลอย", "Sano Loi", "TH1205"),
        area("TH50", "province", "เชียงใหม่", "Chiang Mai"),
        area("TH5001", "amphoe", "เมืองเชียงใหม่", "Mueang Chiang Mai", "TH50"),
        # Same tambon name in two provinces, to test ambiguity.
        area("TH120502", "tambon", "บางรักพัฒนา", "Bang Rak Phatthana", "TH1205"),
        area("TH500199", "tambon", "บางรักพัฒนา", "Bang Rak Phatthana", "TH5001"),
        area("TH10", "province", "กรุงเทพมหานคร", "Bangkok"),
    ]
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("อำเภอบางบัวทอง", ("amphoe", "บางบัวทอง")), ("จ.นนทบุรี", ("province", "นนทบุรี")),
     ("Bang Bua Thong District", ("amphoe", "Bang Bua Thong")), ("นนทบุรี", (None, "นนทบุรี"))],
)
def test_split_prefix(text, expected):
    assert split_prefix(text) == expected


def test_normalize_aliases():
    assert normalize("กทม") == "กรุงเทพมหานคร"
    assert normalize("  Chiang-Mai ") == "chiang mai"


def test_resolve_thai_and_english():
    assert [a.code for a in SMALL.resolve("อำเภอบางบัวทอง")] == ["TH1205"]
    assert [a.code for a in SMALL.resolve("bang bua thong")] == ["TH1205"]
    assert [a.code for a in SMALL.resolve("กรุงเทพ")] == ["TH10"]


def test_muang_spelling_variant():
    assert [a.code for a in SMALL.resolve("Muang Chiang Mai")] == ["TH5001"]


def test_ambiguous_name_returns_all():
    assert len(SMALL.resolve("บางรักพัฒนา")) == 2


def test_phrase_with_province_narrows_ambiguity():
    assert [a.code for a in SMALL.resolve_phrase("ตำบลบางรักพัฒนา จังหวัดนนทบุรี")] == ["TH120502"]


def test_parents_chain():
    tambon = SMALL.by_code["TH120501"]
    assert [p.code for p in SMALL.parents(tambon)] == ["TH1205", "TH12"]


def test_unknown_place():
    assert SMALL.resolve("Atlantis") == []


def test_find_in_thai_text_without_spaces():
    found = SMALL.find_in_text("น้ำจะท่วมอำเภอบางบัวทองไหม")
    assert [a.code for a in found] == ["TH1205"]


def test_find_in_text_most_specific_first_and_province_filter():
    found = SMALL.find_in_text("ตำบลบางรักพัฒนา จังหวัดนนทบุรี ฝนตกไหม")
    # The Chiang Mai tambon with the same name is dropped because Nonthaburi is mentioned.
    assert [a.code for a in found] == ["TH120502", "TH12"]


def test_find_in_english_text():
    assert [a.code for a in SMALL.find_in_text("Will it flood in Bang Bua Thong tomorrow?")] == ["TH1205"]


def test_prefixed_name_beats_longer_overlapping_name():
    gaz = Gazetteer([
        area("TH14", "province", "พระนครศรีอยุธยา", "Phra Nakhon Si Ayutthaya"),
        area("TH1412", "amphoe", "เสนา", "Sena", "TH14"),
        area("TH412001", "tambon", "นาไหม", "Na Mai", "TH4120"),
    ])
    # "ไหม" is a question particle; "นาไหม" overlaps "เสนา" and is longer.
    assert [a.code for a in gaz.find_in_text("น้ำจะท่วมอำเภอเสนาไหม")] == ["TH1412"]


def test_longer_name_wins_over_contained_name():
    # "เมืองเชียงใหม่" contains "เชียงใหม่"; only the longer amphoe should match.
    assert [a.code for a in SMALL.find_in_text("อำเภอเมืองเชียงใหม่ฝุ่นเยอะไหม")] == ["TH5001"]


@pytest.mark.skipif(not REAL.exists(), reason="run scripts/build_admin_areas.py first")
class TestRealGazetteer:
    gaz = Gazetteer.load(REAL) if REAL.exists() else None

    def test_counts(self):
        levels = [a.level for a in self.gaz.areas]
        assert (levels.count("province"), levels.count("amphoe"), levels.count("tambon")) == (77, 928, 7425)

    def test_bang_bua_thong(self):
        found = self.gaz.resolve("อำเภอบางบัวทอง")
        assert len(found) == 1 and found[0].code.startswith("TH12")

    def test_provinces_resolve_uniquely(self):
        for name in ("เชียงใหม่", "Khon Kaen", "สุพรรณบุรี", "กทม", "โคราช"):
            assert len(self.gaz.resolve(name)) >= 1, name
            assert self.gaz.resolve(name)[0].level == "province", name

    @pytest.mark.parametrize(
        ("question", "province"),
        [("น้ำจะท่วมอำเภอบางบัวทองไหม", "TH12"), ("เชียงใหม่มีไฟป่าไหม", "TH50"),
         ("ฝนตกหนักที่หาดใหญ่ไหม", "TH90"), ("Is Khon Kaen in drought?", "TH40")],
    )
    def test_find_in_real_questions(self, question, province):
        found = self.gaz.find_in_text(question)
        assert found, question
        assert found[0].code.startswith(province), [(a.code, a.name_th) for a in found]

    @pytest.mark.parametrize(
        ("question", "code_prefix", "level"),
        [
            ("ฝนตกที่เสนาไหม", "TH1412", "amphoe"),        # particle "ไหม" must not create ต.นาไหม
            ("หาดใหญ่ฝนตกหนักไหม", "TH9011", "amphoe"),   # district beats same-name tambon
            ("โคราชแล้งไหม", "TH30", "province"),          # alias beats a real tambon named โคราช
            ("อยุธยาน้ำท่วมไหม", "TH14", "province"),
            ("สุไหงโกลกฝนตกไหม", "TH9610", "amphoe"),      # official name has a hyphen
            ("ตากมีไฟป่าไหม", "TH63", "province"),         # short name at the start
            ("Is Hat Yai flooding?", "TH9011", "amphoe"),
            ("Hat Yai rain", "TH9011", "amphoe"),          # "rai" inside "rain" is not ต.ไร่
        ],
    )
    def test_hard_questions(self, question, code_prefix, level):
        found = self.gaz.find_in_text(question)
        assert found and found[0].code.startswith(code_prefix) and found[0].level == level, [(a.code, a.name_th) for a in found]

    def test_short_common_word_is_not_a_place(self):
        # "เลย" (= at all) is also Loei province; mid-sentence it must not match.
        assert self.gaz.find_in_text("ฝนตกหนักเลย") == []

    def test_every_area_has_a_point_inside_thailand(self):
        assert all(5.5 < a.lat < 20.6 and 97.2 < a.lon < 105.8 for a in self.gaz.areas)
