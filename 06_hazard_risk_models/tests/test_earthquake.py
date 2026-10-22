import pytest

from risk_models.earthquake import distance_km, mmi, mmi_label_th, shaking_at


def test_distance_bangkok_chiang_mai():
    assert distance_km(13.7563, 100.5018, 18.7883, 98.9853) == pytest.approx(581, abs=5)


@pytest.mark.parametrize(("mag", "r", "expected"), [
    # Hand-computed from the Allen et al. (2012) Rhypo equation.
    (7.7, 1000.0, 3.6),   # Bangkok from the 2025 Sagaing quake (observed IV on the ground, more in towers)
    (7.7, 100.0, 6.6),
    (5.0, 10.0, 6.0),
    (3.0, 100.0, 1.0),   # below 1, clipped: not felt
])
def test_allen_2012_values(mag, r, expected):
    assert mmi(mag, r) == pytest.approx(expected, abs=0.1)


def test_mmi_never_below_one_and_falls_with_distance():
    assert mmi(2.0, 2000.0) == 1.0
    assert mmi(6.0, 20.0) > mmi(6.0, 60.0) > mmi(6.0, 300.0)


def test_shaking_uses_depth_and_default_depth():
    shallow, km = shaking_at(20.0, 99.0, 20.0, 99.0, 5.0, 5.0)
    deep, _ = shaking_at(20.0, 99.0, 20.0, 99.0, 5.0, 100.0)
    assert km == 0 and shallow > deep
    assert shaking_at(20.0, 99.0, 20.0, 99.0, 5.0, None)[0] == pytest.approx(mmi(5.0, 10.0))


def test_labels():
    assert mmi_label_th(1.0) == "ไม่รู้สึก" and mmi_label_th(6.4) == "สั่นแรง อาคารเสียหายเล็กน้อย"
