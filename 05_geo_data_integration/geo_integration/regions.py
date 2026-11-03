"""Six geographic regions for user questions; TMD warning regions remain separate."""

import re

# National Geographic Committee grouping, described by the Royal Society:
# https://www.orst.go.th/iwfm_list.asp?i=0040002504004005/63EHO5037006$@0020
# Bueng Kan (TH38), established after that article's province list, belongs to the northeast.
REGION_CODES = {
    "ภาคเหนือ": ("TH50", "TH51", "TH52", "TH53", "TH54", "TH55", "TH56", "TH57", "TH58"),
    "ภาคกลาง": ("TH10", "TH11", "TH12", "TH13", "TH14", "TH15", "TH16", "TH17", "TH18", "TH19", "TH26",
                "TH60", "TH61", "TH62", "TH64", "TH65", "TH66", "TH67", "TH72", "TH73", "TH74", "TH75"),
    "ภาคตะวันออกเฉียงเหนือ": ("TH30", "TH31", "TH32", "TH33", "TH34", "TH35", "TH36", "TH37", "TH38", "TH39",
                            "TH40", "TH41", "TH42", "TH43", "TH44", "TH45", "TH46", "TH47", "TH48", "TH49"),
    "ภาคตะวันออก": ("TH20", "TH21", "TH22", "TH23", "TH24", "TH25", "TH27"),
    "ภาคตะวันตก": ("TH63", "TH70", "TH71", "TH76", "TH77"),
    "ภาคใต้": ("TH80", "TH81", "TH82", "TH83", "TH84", "TH85", "TH86", "TH90", "TH91", "TH92", "TH93", "TH94", "TH95", "TH96"),
}
REGION_ALIASES = {"ภาคอีสาน": "ภาคตะวันออกเฉียงเหนือ"}
REGION_PATTERN = re.compile("|".join(re.escape(name) for name in sorted([*REGION_CODES, *REGION_ALIASES], key=len, reverse=True)))


def find_regions(text: str) -> list[str]:
    return list(dict.fromkeys(REGION_ALIASES.get(m.group(), m.group()) for m in REGION_PATTERN.finditer(text)))


def mask_regions(text: str) -> str:
    return REGION_PATTERN.sub(lambda m: " " * len(m.group()), text)
