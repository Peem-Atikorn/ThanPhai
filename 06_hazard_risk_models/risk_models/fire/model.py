"""Fire risk model: gradient boosting on hotspot history, compared with two baselines.

Levels come from probability thresholds chosen on the validation year, never on the test year:
WATCH keeps recall >= 0.80, WARNING keeps precision >= 0.70 (both on validation data).
"""

from dataclasses import asdict, dataclass, field

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    precision_recall_curve,
    precision_score,
    recall_score,
    roc_auc_score,
)

from risk_models.fire.dataset import FEATURES

WATCH_TARGET_RECALL = 0.80
WARNING_TARGET_PRECISION = 0.70


@dataclass
class FireModel:
    estimator: HistGradientBoostingClassifier
    watch_threshold: float
    warning_threshold: float
    trained_years: list[int]
    version: str
    feature_names: list[str] = field(default_factory=lambda: list(FEATURES))
    # District order and training-years climatology, needed to build the same features at prediction time.
    amphoe_codes: list[str] = field(default_factory=list)
    clim: np.ndarray | None = None
    metrics: dict = field(default_factory=dict)

    def predict_proba(self, table: pd.DataFrame) -> np.ndarray:
        names = getattr(self, "feature_names", FEATURES)  # models saved before feature_names existed
        return self.estimator.predict_proba(table[names])[:, 1]

    def level(self, p: float) -> str:
        if p >= self.warning_threshold:
            return "WARNING"
        if p >= self.watch_threshold:
            return "WATCH"
        return "NORMAL"

    def save(self, path) -> None:
        joblib.dump(self, path)

    @staticmethod
    def load(path) -> "FireModel":
        return joblib.load(path)


def fit(train: pd.DataFrame, feature_names: list[str] | None = None) -> HistGradientBoostingClassifier:
    feats = feature_names if feature_names is not None else FEATURES
    model = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.08, max_leaf_nodes=31,
                                           l2_regularization=1.0, random_state=0)
    return model.fit(train[feats], train["y"])


def thresholds(y: np.ndarray, p: np.ndarray) -> tuple[float, float]:
    """WATCH: highest threshold that keeps recall >= target. WARNING: lowest threshold >= WATCH with precision >= target.

    One sorted pass via precision_recall_curve. (The first version scored every unique probability with
    recall_score and took ~8 minutes on 340k rows.)
    """
    precision, recall, cut = precision_recall_curve(y, p)
    precision, recall = precision[:-1], recall[:-1]  # align with `cut` (sklearn appends a final point)
    ok_recall = cut[recall >= WATCH_TARGET_RECALL]
    watch = float(ok_recall.max()) if len(ok_recall) else 0.5
    ok_precision = cut[(cut >= watch) & (precision >= WARNING_TARGET_PRECISION)]
    warning = float(ok_precision.min()) if len(ok_precision) else max(watch, 0.9)
    return watch, warning


def scores(y: np.ndarray, p: np.ndarray, threshold: float) -> dict:
    pred = p >= threshold
    return {
        "roc_auc": round(float(roc_auc_score(y, p)), 4),
        "pr_auc": round(float(average_precision_score(y, p)), 4),
        "brier": round(float(brier_score_loss(y, np.clip(p, 0, 1))), 4),
        "recall": round(float(recall_score(y, pred, zero_division=0)), 4),
        "precision": round(float(precision_score(y, pred, zero_division=0)), 4),
        "alerts_per_1000_rows": round(float(pred.mean() * 1000), 2),
    }


@dataclass
class Report:
    rows: dict
    positive_rate: dict
    thresholds: dict
    test: dict  # name -> scores
    feature_importance: dict

    def as_dict(self) -> dict:
        return asdict(self)
