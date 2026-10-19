"""Standardized Precipitation Index (SPI), McKee et al. (1993).

For each calendar month, fit a gamma distribution to the rolling n-month rainfall totals of a reference
period, then map each total to a standard normal value. Zero totals are handled with a mixed
distribution: H(x) = q + (1 - q) * G(x), where q is the share of zero totals.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date

import numpy as np
from scipy import stats

# Probabilities are clipped so a single extreme month does not produce an infinite SPI.
PROB_CLIP = (1e-6, 1 - 1e-6)
MIN_YEARS = 20


@dataclass(frozen=True)
class SpiValue:
    month: date
    total_mm: float
    spi: float | None


def rolling_totals(monthly: list[tuple[date, float]], scale: int) -> list[tuple[date, float | None]]:
    """Sum of the current month and the previous scale-1 months. None until enough history."""
    values = [v for _, v in monthly]
    out = []
    for i, (month, _) in enumerate(monthly):
        window = values[max(0, i - scale + 1) : i + 1]
        out.append((month, float(sum(window)) if len(window) == scale and None not in window else None))
    return out


def compute_spi(monthly: list[tuple[date, float]], scale: int = 3, reference: tuple[int, int] | None = None) -> list[SpiValue]:
    """monthly: (first day of month, rainfall mm) sorted by month, without gaps."""
    totals = rolling_totals(monthly, scale)
    by_month: dict[int, list[float]] = defaultdict(list)
    for month, total in totals:
        if total is None:
            continue
        if reference and not (reference[0] <= month.year <= reference[1]):
            continue
        by_month[month.month].append(total)

    fits = {}
    for cal_month, samples in by_month.items():
        if len(samples) < MIN_YEARS:
            continue
        arr = np.asarray(samples)
        positive = arr[arr > 0]
        q = 1 - len(positive) / len(arr)
        if len(positive) < 3 or np.allclose(positive, positive[0]):
            continue
        shape, _, scale_param = stats.gamma.fit(positive, floc=0)
        fits[cal_month] = (q, shape, scale_param)

    result = []
    for month, total in totals:
        fit = fits.get(month.month)
        if total is None or fit is None:
            result.append(SpiValue(month, total if total is not None else float("nan"), None))
            continue
        q, shape, scale_param = fit
        prob = q + (1 - q) * stats.gamma.cdf(total, shape, loc=0, scale=scale_param) if total > 0 else q
        prob = float(np.clip(prob, *PROB_CLIP))
        result.append(SpiValue(month, total, round(float(stats.norm.ppf(prob)), 3)))
    return result


def classify_spi(spi: float | None) -> str:
    """Standard SPI drought classes."""
    if spi is None:
        return "no_data"
    if spi <= -2.0:
        return "extreme_drought"
    if spi <= -1.5:
        return "severe_drought"
    if spi <= -1.0:
        return "moderate_drought"
    if spi < 1.0:
        return "near_normal"
    return "wet"
