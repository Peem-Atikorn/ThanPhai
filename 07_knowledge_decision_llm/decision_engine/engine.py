"""Deterministic alert-level decision. The LLM explains this result; it never changes it."""

import operator
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from thanphai_shared import AlertLevel, HazardType

RULES_PATH = Path(__file__).with_name("rules.yaml")
OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le}


@dataclass
class DecisionInput:
    hazard: HazardType
    model_level: AlertLevel | None = None
    model_confidence: float | None = None
    official_warning_active: bool = False
    observations: dict[str, float | None] = field(default_factory=dict)
    degraded_sources: list[str] = field(default_factory=list)


@dataclass
class AlertDecision:
    hazard: HazardType
    # None means there was no usable input at all. Show "no data", never "normal".
    level: AlertLevel | None
    confidence: float
    rule_ids: list[str]
    notices: list[str]
    rules_version: str


class DecisionEngine:
    def __init__(self, rules: dict | None = None):
        self.rules = rules if rules is not None else yaml.safe_load(RULES_PATH.read_text(encoding="utf-8"))
        self.version = str(self.rules["version"])

    def decide(self, data: DecisionInput) -> AlertDecision:
        candidates: list[tuple[AlertLevel, float, str]] = []
        conf = self.rules["confidence"]

        if data.official_warning_active:
            level = AlertLevel[self.rules["official_warning"]["min_level"]]
            candidates.append((level, conf["official"], "OFFICIAL_WARNING"))

        hazard_rules = self.rules["overrides"].get(data.hazard.value, [])
        for rule in hazard_rules:
            value = data.observations.get(rule["variable"])
            if value is not None and OPS[rule["op"]](value, rule["threshold"]):
                candidates.append((AlertLevel[rule["level"]], conf["override"], rule["id"]))

        if data.model_level is not None:
            model_conf = data.model_confidence if data.model_confidence is not None else 0.5
            candidates.append((data.model_level, model_conf, f"MODEL_{data.model_level.name}"))

        notices = [f"ข้อมูลจาก {name} ไม่พร้อมใช้งานหรือไม่เป็นปัจจุบัน" for name in data.degraded_sources]
        # Only variables that this hazard's rules read count as evidence. Heavy rain alone must not
        # produce a "normal" flood result when no water-level data is available.
        relevant = {rule["variable"] for rule in hazard_rules}
        has_observations = any(data.observations.get(v) is not None for v in relevant)

        if not candidates:
            if not has_observations:
                return AlertDecision(data.hazard, None, 0.0, [], notices + ["ไม่มีข้อมูลเพียงพอสำหรับประเมิน"], self.version)
            # Observations exist but no rule fired and there is no model output: a rules-only NORMAL.
            candidates.append((AlertLevel.NORMAL, 0.5, "RULES_NO_TRIGGER"))

        level = max(c[0] for c in candidates)
        # Confidence comes from the strongest evidence that supports the final level.
        confidence = max(c[1] for c in candidates if c[0] == level)
        if data.degraded_sources:
            confidence *= conf["degraded_factor"]
        rule_ids = [c[2] for c in sorted(candidates, key=lambda c: c[0], reverse=True)]
        return AlertDecision(data.hazard, level, round(confidence, 3), rule_ids, notices, self.version)
