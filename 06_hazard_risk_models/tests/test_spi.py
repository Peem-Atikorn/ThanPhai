from datetime import date

import numpy as np
import pytest

from risk_models.drought import classify_spi, compute_spi
from risk_models.drought.history import monthly_from_daily
from risk_models.drought.spi import rolling_totals


def synthetic_months(years: int = 30, seed: int = 0) -> list[tuple[date, float]]:
    rng = np.random.default_rng(seed)
    # Thai-like seasonality: dry Nov–Apr, wet May–Oct.
    mean = [10, 15, 30, 60, 150, 160, 170, 190, 250, 180, 50, 15]
    out = []
    for y in range(1990, 1990 + years):
        for m in range(1, 13):
            out.append((date(y, m, 1), float(rng.gamma(2.0, mean[m - 1] / 2.0))))
    return out


def test_rolling_totals_waits_for_full_window():
    months = [(date(2020, m, 1), float(m)) for m in range(1, 5)]
    totals = rolling_totals(months, 3)
    assert totals[0][1] is None and totals[1][1] is None
    assert totals[2][1] == 6.0 and totals[3][1] == 9.0


def test_spi_is_roughly_standard_normal():
    values = [v.spi for v in compute_spi(synthetic_months(), scale=3) if v.spi is not None]
    assert len(values) > 300
    assert abs(np.mean(values)) < 0.15
    assert 0.8 < np.std(values) < 1.2


def test_very_dry_season_gives_drought_class():
    months = synthetic_months()
    # Make the last 3 months almost rainless.
    for i in range(-3, 0):
        months[i] = (months[i][0], 0.5)
    last = compute_spi(months, scale=3)[-1]
    assert last.spi is not None and last.spi <= -1.5
    assert classify_spi(last.spi) in ("severe_drought", "extreme_drought")


def test_short_history_returns_none():
    assert all(v.spi is None for v in compute_spi(synthetic_months(years=5)))


def test_zero_totals_do_not_break_fit():
    months = synthetic_months()
    months = [(m, 0.0 if m.month in (1, 2) else v) for m, v in months]
    spi = compute_spi(months, scale=1)
    assert all(np.isfinite(v.spi) for v in spi if v.spi is not None)


@pytest.mark.parametrize(
    ("value", "label"),
    [(None, "no_data"), (-2.1, "extreme_drought"), (-1.7, "severe_drought"), (-1.2, "moderate_drought"), (0.0, "near_normal"), (1.3, "wet")],
)
def test_classify_spi(value, label):
    assert classify_spi(value) == label


def test_monthly_from_daily_drops_partial_last_month():
    days = ["2024-01-30", "2024-01-31", "2024-02-01", "2024-02-02"]
    assert monthly_from_daily(days, [1.0, 2.0, 3.0, None]) == [(date(2024, 1, 1), 3.0)]


def test_monthly_from_daily_drops_month_that_stops_early():
    # Series ends on 12 Oct with no missing values: October is still incomplete.
    days = ["2026-09-29", "2026-09-30", "2026-10-11", "2026-10-12"]
    assert monthly_from_daily(days, [1.0, 2.0, 3.0, 4.0]) == [(date(2026, 9, 1), 3.0)]


def test_monthly_from_daily_keeps_complete_last_month():
    days = ["2026-09-29", "2026-09-30"]
    assert monthly_from_daily(days, [1.0, 2.0]) == [(date(2026, 9, 1), 3.0)]


def test_monthly_from_daily_rejects_inner_gap():
    with pytest.raises(ValueError):
        monthly_from_daily(["2024-01-31", "2024-02-01", "2024-03-01"], [None, 1.0, 1.0])
