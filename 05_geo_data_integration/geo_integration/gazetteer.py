"""Resolve Thai or English place names to admin areas, offline and without rate limits."""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from pythainlp.corpus import thai_words
from pythainlp.tokenize import word_tokenize
from pythainlp.util import dict_trie

from geo_integration.regions import REGION_CODES, find_regions, mask_regions

# Admin prefixes people type before a name. Longest first so "อำเภอ" wins over "อ.".
TH_PREFIXES = ("จังหวัด", "อำเภอ", "ตำบล", "แขวง", "เขต", "กิ่งอำเภอ", "จ.", "อ.", "ต.")
EN_PREFIXES = ("province", "district", "subdistrict", "amphoe", "tambon", "khet", "khwaeng", "changwat")
LEVEL_HINTS = {
    "จังหวัด": "province", "จ.": "province", "province": "province", "changwat": "province",
    "อำเภอ": "amphoe", "อ.": "amphoe", "เขต": "amphoe", "กิ่งอำเภอ": "amphoe", "district": "amphoe", "amphoe": "amphoe", "khet": "amphoe",
    "ตำบล": "tambon", "ต.": "tambon", "แขวง": "tambon", "subdistrict": "tambon", "tambon": "tambon", "khwaeng": "tambon",
}
# Common short names -> official name. Used both for direct lookup and for finding names in text.
ALIASES = {
    "กทม": "กรุงเทพมหานคร", "กรุงเทพ": "กรุงเทพมหานคร", "กรุงเทพฯ": "กรุงเทพมหานคร", "bkk": "bangkok",
    "โคราช": "นครราชสีมา", "korat": "nakhon ratchasima", "อยุธยา": "พระนครศรีอยุธยา", "ayutthaya": "phra nakhon si ayutthaya",
    "โกลก": "สุไหงโก-ลก", "สุไหงโกลก": "สุไหงโก-ลก",
}
LEVEL_RANK = {"province": 0, "amphoe": 1, "tambon": 2}
# Thai question particles that often stick to the end of a place name ("เสนาไหม").
PARTICLES = ("ไหม", "มั้ย", "มั๊ย", "หรือเปล่า", "หรือไม่", "บ้าง", "ครับ", "ค่ะ", "คะ")
MUEANG_QUERY = re.compile(r"(?:อำเภอ|อ\.)\s*เมือง")
SHORT_TAMBON = 5


@dataclass(frozen=True)
class Area:
    code: str
    level: str
    name_th: str
    name_en: str
    parent_code: str | None
    lat: float
    lon: float
    area_km2: float


def normalize(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[\s\-_.,()]+", " ", text).strip()
    return ALIASES.get(text.replace(" ", ""), ALIASES.get(text, text))


def split_prefix(text: str) -> tuple[str | None, str]:
    raw = text.strip()
    for prefix in TH_PREFIXES:
        if raw.startswith(prefix):
            return LEVEL_HINTS[prefix], raw[len(prefix) :].strip()
    lowered = raw.lower()
    for prefix in EN_PREFIXES:
        if lowered.startswith(prefix + " "):
            return LEVEL_HINTS[prefix], raw[len(prefix) :].strip()
        if lowered.endswith(" " + prefix):
            return LEVEL_HINTS[prefix], raw[: -len(prefix)].strip()
    return None, raw


class Gazetteer:
    def __init__(self, areas: list[Area]):
        self.areas = areas
        self._trie = dict_trie(set(thai_words()) | {area.name_th for area in areas})
        self.by_code = {a.code: a for a in areas}
        self.index: dict[str, list[Area]] = {}
        for area in areas:
            for name in self._keys(area):
                self.index.setdefault(name, []).append(area)
        for alias, official in ALIASES.items():
            target = self.index.get(normalize(official)) or self.index.get(official.replace("-", ""))
            if target:
                # Merge, don't skip: a real tambon named "โคราช" exists, but people mean the province.
                existing = self.index.setdefault(alias, [])
                existing.extend(a for a in target if a not in existing)

    @classmethod
    def load(cls, path: Path) -> "Gazetteer":
        rows = json.loads(path.read_text(encoding="utf-8"))
        return cls([Area(**row) for row in rows])

    @staticmethod
    def _keys(area: Area) -> set[str]:
        keys = {normalize(area.name_th), normalize(area.name_en)}
        # Official Thai names can contain hyphens or spaces ("สุไหงโก-ลก"); free text usually has neither.
        keys.add(re.sub(r"[\s\-]", "", area.name_th))
        # HDX amphoe names often start with "Mueang"; people also write "Muang".
        if area.name_en.lower().startswith("mueang "):
            keys.add(normalize("muang " + area.name_en[7:]))
        return {k for k in keys if k}

    def parents(self, area: Area) -> list[Area]:
        chain = []
        code = area.parent_code
        while code and code in self.by_code:
            chain.append(self.by_code[code])
            code = self.by_code[code].parent_code
        return chain

    def resolve(self, text: str, within: str | None = None) -> list[Area]:
        """Return matching areas, most general level first. More than one result means ambiguous."""
        level_hint, name = split_prefix(text)
        matches = list(self.index.get(normalize(name), []))
        if level_hint:
            matches = [a for a in matches if a.level == level_hint]
        if within:
            matches = [a for a in matches if a.code.startswith(within)]
        return sorted(matches, key=lambda a: (LEVEL_RANK[a.level], a.code))

    def find_in_text(self, text: str) -> list[Area]:
        """Find places mentioned inside free text such as "น้ำจะท่วมอำเภอบางบัวทองไหม".

        Thai token boundaries reject names inside ordinary words. Longer names win over names they
        contain, an admin prefix restricts the level, and short tambons need an explicit prefix or
        a following parent area. Region questions return provinces; explicit local areas take priority.
        """
        regions = find_regions(text)
        lowered = mask_regions(text).lower().strip()
        boundaries = {0}
        offset = 0
        for token in word_tokenize(lowered, custom_dict=self._trie, engine="newmm", keep_whitespace=True):
            offset += len(token)
            boundaries.add(offset)
        particle_spans = [(m.start(), m.end()) for p in PARTICLES for m in re.finditer(re.escape(p), lowered)]
        boundaries.update(start for start, _ in particle_spans)
        hits: list[tuple[int, int, str | None, list[Area]]] = []
        for key, areas in self.index.items():
            # Cheap substring test first: building a regex for all ~12,000 names took ~0.8 s per question.
            if key not in lowered:
                continue
            # Short names ("นา", "เลย" = "at all") match inside ordinary words. Accept them only after an
            # admin prefix or at the very start of the question ("ตากมีไฟป่าไหม").
            min_len = 3 if key.isascii() else 4
            # English names also need letter boundaries ("rai" inside "rain").
            pattern = rf"(?<![a-z]){re.escape(key)}(?![a-z])" if key.isascii() else re.escape(key)
            for match in re.finditer(pattern, lowered):
                start = match.start()
                level = self._prefix_level_before(lowered, start, boundaries)
                if level and not any(a.level == level for a in areas):
                    continue  # An explicit district prefix must not select a same-name tambon.
                if level is None and (start not in boundaries or match.end() not in boundaries):
                    continue
                dam_prefix = re.search(r"(?:เขื่อน|อ่างเก็บน้ำ)\s*$", lowered[:start])
                if level is None and dam_prefix and dam_prefix.start() in boundaries:
                    continue
                if len(key) >= min_len or level or (start == 0 and len(key) >= 2) or any(a.level == "tambon" for a in areas):
                    hits.append((start, start + len(key), level, areas))

        def eats_particle(hit) -> bool:
            # "นาไหม" inside "เสนาไหม" swallows the particle "ไหม"; such a match is a weak guess.
            return hit[2] is None and any(hit[0] < p_end and p_start < hit[1] for p_start, p_end in particle_spans)

        # Ranking: a name right after its admin prefix wins ("อำเภอเสนาไหม" -> อ.เสนา, not ต.นาไหม),
        # then matches that do not swallow a particle, then longer names over overlapping shorter ones.
        hits.sort(key=lambda h: (h[2] is None, eats_particle(h), -(h[1] - h[0]), h[0]))
        kept: list[tuple[int, int, str | None, list[Area]]] = []
        for hit in hits:
            if all(hit[1] <= k[0] or hit[0] >= k[1] for k in kept):
                kept.append(hit)
        found: list[Area] = []
        scopes = [(start, a.code) for start, _, level, matches in kept for a in matches
                  if a.level in ("amphoe", "province") and (level is None or a.level == level)]
        # `kept` is already in match-quality order, so the best match comes first.
        for start, end, level, areas in kept:
            if level is not None:
                found.extend(a for a in areas if a.level == level)
            else:
                # Same name at several levels (amphoe หาดใหญ่ and its tambon หาดใหญ่): people mean the larger area.
                top = min(LEVEL_RANK[a.level] for a in areas)
                for area in areas:
                    if LEVEL_RANK[area.level] != top:
                        continue
                    if area.level == "tambon" and end - start <= SHORT_TAMBON:
                        parent_codes = {p.code for p in self.parents(area)}
                        if not any(pos >= end and code in parent_codes for pos, code in scopes):
                            continue
                    found.append(area)
        provinces = {a.code for a in found if a.level == "province"}
        if provinces:
            found = [a for a in found if a.level == "province" or a.code[:4] in provinces]
        ordered = list({a.code: a for a in found}.values())

        if MUEANG_QUERY.search(lowered) and not any(a.level == "amphoe" and a.name_th.startswith("เมือง") for a in ordered):
            provinces = [a for a in ordered if a.level == "province"]
            if len(provinces) == 1:
                cities = [a for a in self.areas if a.level == "amphoe" and a.parent_code == provinces[0].code
                          and a.name_th.startswith("เมือง")]
                if len(cities) == 1:
                    ordered.insert(0, cities[0])

        if not ordered and regions:
            codes = {code for region in regions for code in REGION_CODES[region]}
            return [self.by_code[code] for code in sorted(codes) if code in self.by_code and self.by_code[code].level == "province"]

        def lineage_key(area: Area) -> tuple[int, int]:
            # Group each area with its ancestors/descendants at the position of the group's best match,
            # then put the most specific area first ("ตำบลX จังหวัดY" -> tambon, then province).
            # Unrelated matches keep their match-quality order.
            group = min(i for i, other in enumerate(ordered) if other.code.startswith(area.code) or area.code.startswith(other.code))
            return group, -LEVEL_RANK[area.level]

        return sorted(ordered, key=lineage_key)

    @staticmethod
    def _prefix_level_before(text: str, start: int, boundaries: set[int]) -> str | None:
        before = text[:start].rstrip()
        for prefix in TH_PREFIXES:
            prefix_start = len(before) - len(prefix)
            if prefix_start in boundaries and before.endswith(prefix):
                return LEVEL_HINTS[prefix]
        for prefix in EN_PREFIXES:
            prefix_start = len(before) - len(prefix)
            if prefix_start in boundaries and before.endswith(prefix):
                return LEVEL_HINTS[prefix]
        return None

    def resolve_phrase(self, text: str) -> list[Area]:
        """Resolve "อำเภอบางบัวทอง จังหวัดนนทบุรี" style phrases, using the province to narrow the result."""
        parts = re.split(r"\s+(?=จังหวัด|จ\.|อำเภอ|อ\.|เขต|ตำบล|ต\.|แขวง)|,\s*", text.strip())
        parts = [p for p in parts if p]
        if len(parts) == 1:
            return self.resolve(parts[0])
        # The most general part is usually last in Thai addresses; narrow the scope from there.
        scope: str | None = None
        for part in reversed(parts[1:]):
            found = self.resolve(part, within=scope)
            if len(found) == 1:
                scope = found[0].code
        return self.resolve(parts[0], within=scope)
