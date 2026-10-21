import pytest

from decision_engine import DecisionEngine, DecisionInput
from thanphai_shared import AlertLevel, HazardType

engine = DecisionEngine()
L = AlertLevel


def decide(hazard=HazardType.FLOOD, **kwargs):
    return engine.decide(DecisionInput(hazard=hazard, **kwargs))


def test_no_input_means_no_data_not_normal():
    d = decide()
    assert d.level is None and d.confidence == 0.0


def test_observations_without_trigger_give_normal():
    d = decide(observations={"above_bank_m": -2.0})
    assert d.level == L.NORMAL and d.rule_ids == ["RULES_NO_TRIGGER"]


def test_official_warning_sets_minimum_level():
    d = decide(official_warning_active=True, model_level=L.NORMAL, model_confidence=0.9)
    assert d.level == L.WARNING and d.rule_ids[0] == "OFFICIAL_WARNING"


def test_model_cannot_lower_official_warning():
    assert decide(official_warning_active=True, model_level=L.WATCH).level == L.WARNING


def test_model_can_raise_above_official():
    assert decide(official_warning_active=True, model_level=L.CRITICAL).level == L.CRITICAL


@pytest.mark.parametrize(
    ("above_bank_m", "expected"),
    [(-1.0, L.NORMAL), (-0.3, L.WATCH), (0.0, L.WATCH), (0.01, L.WARNING), (0.5, L.CRITICAL), (1.21, L.CRITICAL)],
)
def test_flood_bank_thresholds(above_bank_m, expected):
    assert decide(observations={"above_bank_m": above_bank_m}).level == expected


@pytest.mark.parametrize(("rain", "expected"), [(10, L.NORMAL), (35.1, L.WATCH), (90, L.WATCH), (90.1, L.WARNING)])
def test_rain_tmd_thresholds(rain, expected):
    assert decide(HazardType.RAIN_STORM, observations={"rain_24h": rain}).level == expected


@pytest.mark.parametrize(("spi", "expected"), [(0.2, L.NORMAL), (-1.0, L.WATCH), (-1.6, L.WARNING), (-2.3, L.CRITICAL)])
def test_drought_spi_classes(spi, expected):
    assert decide(HazardType.DROUGHT, observations={"spi_3": spi}).level == expected


@pytest.mark.parametrize(("rain", "expected"), [(0.2, L.NORMAL), (35.1, L.WATCH), (120, L.WARNING)])
def test_forecast_rain_thresholds(rain, expected):
    d = decide(HazardType.RAIN_STORM, observations={"forecast_rain_24h": rain})
    assert d.level == expected


@pytest.mark.parametrize(("cells", "expected"), [(0, L.NORMAL), (1, L.WATCH), (999, L.WATCH), (1558, L.WARNING)])
def test_satellite_flood_cells(cells, expected):
    assert decide(observations={"satellite_flood_cells_7d": cells}).level == expected


def test_station_overflow_beats_small_satellite_signal():
    d = decide(observations={"above_bank_m": 2.11, "satellite_flood_cells_7d": 20})
    assert d.level == L.CRITICAL and d.rule_ids[0] == "FLOOD_ABOVE_BANK_HIGH"


@pytest.mark.parametrize(("vci", "expected"), [(80, L.NORMAL), (35, L.NORMAL), (34.9, L.WATCH), (5, L.WATCH)])
def test_vegetation_alone_gives_at_most_watch(vci, expected):
    assert decide(HazardType.DROUGHT, observations={"vci": vci}).level == expected


def test_normal_vegetation_is_normal_not_no_data():
    # vci is withheld when the drop is tiny, but the deficit itself still says "measured and fine".
    assert decide(HazardType.DROUGHT, observations={"vci": None, "ndvi_deficit": 0.01}).level == L.NORMAL


def test_spi_can_still_raise_drought_above_watch():
    assert decide(HazardType.DROUGHT, observations={"vci": 5, "spi_3": -2.2}).level == L.CRITICAL


def test_fire_hotspots():
    assert decide(HazardType.FIRE, observations={"hotspots_24h": 12}).level == L.WARNING


def test_override_beats_low_model():
    d = decide(model_level=L.NORMAL, model_confidence=0.99, observations={"above_bank_m": 0.2})
    assert d.level == L.WARNING and "FLOOD_ABOVE_BANK" in d.rule_ids


def test_rules_do_not_cross_hazards():
    # Rain observations must not trigger flood rules.
    assert decide(HazardType.FLOOD, observations={"rain_24h": 200}).level is None


def test_degraded_lowers_confidence_never_level():
    normal = decide(model_level=L.WARNING, model_confidence=0.8)
    degraded = decide(model_level=L.WARNING, model_confidence=0.8, degraded_sources=["thaiwater"])
    assert degraded.level == normal.level == L.WARNING
    assert degraded.confidence < normal.confidence
    assert any("thaiwater" in n for n in degraded.notices)


def test_none_observation_is_ignored():
    assert decide(observations={"above_bank_m": None}).level is None


def test_rules_version_is_reported():
    assert decide(model_level=L.WATCH).rules_version == engine.version


@pytest.mark.parametrize(("pm25", "expected"), [(15.0, L.NORMAL), (37.5, L.NORMAL), (37.6, L.WATCH), (75.0, L.WATCH), (75.1, L.WARNING), (300.0, L.WARNING)])
def test_pm25_pcd_bands(pm25, expected):
    assert decide(HazardType.AIR_QUALITY, observations={"pm25_24h": pm25}).level == expected


def test_pm25_model_alone_gives_at_most_watch():
    assert decide(HazardType.AIR_QUALITY, observations={"pm25_model_24h": 120.0}).level == L.WATCH
    assert decide(HazardType.AIR_QUALITY, observations={"pm25_model_24h": 20.0}).level == L.NORMAL


def test_pm25_without_any_value_is_no_data():
    assert decide(HazardType.AIR_QUALITY, observations={"pm25_24h": None, "pm25_model_24h": None}).level is None


@pytest.mark.parametrize(("hi", "expected"), [(30.0, L.NORMAL), (32.9, L.NORMAL), (33.0, L.WATCH), (41.9, L.WATCH),
                                              (42.0, L.WARNING), (51.9, L.WARNING), (52.0, L.CRITICAL)])
def test_heat_index_bands(hi, expected):
    assert decide(HazardType.HEAT, observations={"heat_index_max_24h": hi}).level == expected
