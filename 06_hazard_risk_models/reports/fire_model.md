# Fire risk model — report (2026-10-19)

**Question the model answers:** will this district have **3 or more vegetation-fire hotspots in the next 7 days?**
(3 is the same count as the WATCH rule.)

## Data
- NASA FIRMS VIIRS S-NPP archive (standard processing), Thailand bounding box, 2021-01-01 to 2025-12-31
  — 366 downloads of 5 days each, 18 MB gzip (`scripts/download_firms_history.py`).
- Nominal/high confidence only. **Industrial heat removed:** FIRMS `type = 2` points (cement kilns, refineries)
  and every point in those 74 ~1 km cells (+ neighbours). 1,552,006 of 1,566,481 detections kept.
- 34 % of the bounding-box points fall inside Thailand's districts (the rest are Myanmar, Laos, Cambodia).
- One row per district per day: 928 districts × 1,797 days.

## Features (all use data up to day *t* only)
Hotspots today / last 3 / 7 / 30 days, fire radiative power last 7 days, province hotspots last 7 days,
day of year (sin, cos), and the district's average daily hotspots for that calendar month in the **training years only**.

## Split (by time, no shuffling)
| Set | Years | Rows | Positive rate |
|---|---|---|---|
| Train | 2021–2023 | 989,248 | 11.6 % |
| Validation (thresholds) | 2024 | 339,648 | 14.7 % |
| **Test (reported)** | **2025** | **332,224** | **11.5 %** |

## Results on 2025 (never used for fitting or tuning)
| Method | ROC-AUC | PR-AUC | Recall | Precision |
|---|---|---|---|---|
| **Gradient boosting (this model)** | **0.947** | **0.729** | **0.846** | 0.527 |
| Seasonal climatology | 0.935 | 0.627 | 0.863 | 0.462 |
| Rule: ≥3 hotspots in last 7 days | 0.877 | 0.622 | 0.631 | 0.625 |
| Rule: ≥3 hotspots today (old WATCH rule) | 0.692 | 0.391 | 0.189 | 0.851 |

- The old rule raised WATCH for only **19 %** of districts that then burned; the model catches **85 %**.
- Levels: WATCH when p ≥ 0.191 (recall ≥ 0.80 on 2024), WARNING when p ≥ 0.396 (precision ≥ 0.70 on 2024).
- Brier score 0.053 (climatology 0.070): probabilities are better calibrated than the baseline.
- Most important feature by far: the seasonal climatology (0.53 drop in PR-AUC when shuffled). Recent
  hotspots add the rest — the model is mostly "seasonal pattern + what is burning now".

## How it is used
`HazardService.refresh_fire()` fetches the last 30 days of FIRMS NRT (6 calls), removes static sources,
builds the same features for today, and passes the model level and confidence into the decision engine.
Rules still apply: the model can raise the fire level but never lowers a rule result.

## Limits
- No weather inputs (humidity, rain, wind, drought): per-district weather history was not affordable on
  the free Open-Meteo quota. Adding TMD NWP humidity/rain forecasts is the next improvement.
- Hotspots are a proxy for fire, not burned area; small or cloud-covered fires are missed.
- Retrain every year (`scripts/train_fire_model.py`, ~80 s); the scikit-learn version is pinned to 1.9.1
  so the saved model loads the same way in Docker.
