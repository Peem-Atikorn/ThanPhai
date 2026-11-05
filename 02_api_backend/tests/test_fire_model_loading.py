"""Tests for fire model loading and compatibility safeguards in HazardService."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np

from api_backend.hazard_service import HazardService
from risk_models.fire.dataset import EXTENDED_FEATURES
from risk_models.fire.model import FireModel


def test_load_fire_model_rejects_extended_model(tmp_path: Path):
    models_dir = tmp_path / "models"
    models_dir.mkdir()

    clim = np.full((12, 1), 0.5, dtype=np.float32)

    # 1. Extended model with unsupported features must return None (fallback to rules)
    model_ext = FireModel(
        estimator=None,
        watch_threshold=0.3,
        warning_threshold=0.6,
        trained_years=[2023],
        version="ext-v1",
        amphoe_codes=["TH1000"],
        clim=clim,
        feature_names=EXTENDED_FEATURES,
    )
    model_ext.save(models_dir / "fire_model.joblib")
    assert HazardService._load_fire_model(SimpleNamespace(data_dir=tmp_path)) is None

    # 2. Baseline model with supported features loads successfully
    model_base = FireModel(
        estimator=None,
        watch_threshold=0.3,
        warning_threshold=0.6,
        trained_years=[2023],
        version="base-v1",
        amphoe_codes=["TH1000"],
        clim=clim,
    )
    model_base.save(models_dir / "fire_model.joblib")
    loaded = HazardService._load_fire_model(SimpleNamespace(data_dir=tmp_path))
    assert loaded is not None
    assert loaded.version == "base-v1"
