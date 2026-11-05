# Water-level and dam-inflow models — report (2026-11-05)

**Questions the models answer**
1. **Rivers:** how much will the water level at this station change in the next **6, 12 and 24 hours** (m)?
   From that, will water that is below the bank now be **over the bank** by then?
2. **Dams:** how much water will flow into this dam in the **next 7 days** (million m³)?

Neither model sets an alert level. Levels still come only from `rules.yaml`; nothing in the app uses these models yet.

## Data (ThaiWater, `scripts/download_water_history.py`, 94 min, 75 MB gzip, no failed request)
- **Rivers:** 311 stations with a trusted bank level and a flow gauge or RID key-station status (289 RID, 15 EGAT,
  6 HII, 1 DWR), grouped like the app's river view. Five years, 2021-11-06 to 2026-11-05, from
  `public/waterlevel_graph`, one request per calendar quarter. RID stations report about hourly, HII every 10
  minutes, EGAT every 15. 307 stations keep at least 60 days after cleaning; 8.5 million station-hours.
- **Dams:** all 35 large dams, daily inflow, release and storage from `analyst/dam_yearly_graph`.
  Used from 2004 to 2026-11-04 (199,142 dam-days).
- **Rain:** hourly precipitation (mm, Thai local time) from the Open-Meteo archive (ERA5) for each station's 0.5°
  cell: 128 cells, 2021-10-30 to 2026-11-05, 16 MB, 4 minutes. **Cost: 1,675.9 weighted calls** of the free tier's
  10,000 a day, summed from Open-Meteo's `X-RateLimit-Cost` header (one point, one year of hours = 2.6 calls).
  ThaiWater has no rain history endpoint (`rain_graph` → 404).

### Cleaning (each found in this data)
| Problem | Example | Fix |
|---|---|---|
| Single spike | one reading 4 m off that comes straight back | dropped when > 1.5 m from both neighbours |
| Datum change | stations that move 20-30 m within an hour and stay there; อ.นครชัยศรี went from −9.1 to 0.7 m after 15 hours off | only the readings after the last lasting step (> 3 m, the week after vs the week before, in time, not in readings), unless the flow at least doubled with a rise or halved with a fall and both weeks are within the bank range: then it is water (K.37 บ้านวังเย็น, 456 → 1,460 m³/s in September 2026). Flow is often computed from the level, so it alone is not enough: บ้านท่าบุญมี "rose" 15 → 79 m beside an 18 m bank with flow 1.4 → 71 m³/s |
| Run of bad readings | Ct.4 sat 20 m low for 12 hours on 2023-04-04 | dropped when > 5 m from the median of the 49 hours around it, unless it is above that median and was entered and left without a jump of more than 3 m in an hour (a flash flood); 272 of the 283 such runs in this data start or end with a jump of 5 m or more |
| Gaps | stations that report once a day or only by day | filled only when 3 hours or shorter; every lag and target is a whole number of hours, so none reaches across a gap |
| "No reading" marker | MKSND02 reports `9999.9` | values ≥ 999 dropped |
| Implausible bank | X.258: bank 18.4 m, water at ~51 m | readings outside bank −40 m … bank +15 m dropped |
| Dam running totals | 2002-2003 hold the total since 1 January (Bhumibol 3,946 → 4,005) | dam data from 2004 only; a day above 5 × the dam's 99th percentile (training years) is dropped |

After cleaning, the largest 24-hour rise left in the river data is 8.9 m: สะพานท่าลี่ (ลุ่มน้ำน่าน) reached 196.3 m on
2025-07-23, 3.5 m over its 192.8 m bank. A flood like that is possible; a 20 m step is not.

A first version judged a step on the 24 readings around it. At stations that report every 10-15 minutes that is only
4-6 hours, so floods counted as datum changes, and everything before them was dropped at 40 stations (418,000
station-hours). A second version still had three problems, fixed after review: a station's hours outside the bank
range were removed from the series instead of left empty, so "6 hours ago" and "in 6 hours" could reach across a gap
of days; `interpolate(limit=3)` filled the first 3 hours of every longer gap; and floods that rose while the sensor
was off (judged by flow now) or that rose more than 5 m (judged by how smoothly now) were cut as bad data. A second
review closed smaller leaks: the dam spike limit and storage capacity came from all years (now training years only),
a row near the end of a period had a target inside the next one (now left out), stations with no basin name shared
one "basin", and missing ERA5 hours counted as 0 mm. The numbers below are from the fixed version.
Years before 2018 have many missing dam days; rows whose 7-day target has a gap are not used.

## Features (data up to hour/day *t* only)
- **Rivers:** level change over the last 1, 3, 6, 12, 24, 48, 72 h; level minus bank; spread of the last 24 h;
  mean 6-h and 24-h change of the *other* stations in the same basin (upstream water shows up there first; empty
  for a station with no basin name); rain at the station's cell in the last 6, 24 and 72 h; mean rain over the
  basin's stations in the last 24 and 72 h (a sum is empty unless every hour of it is known);
  day of year and hour (sin, cos). The target is the change, not the level, so one model serves every station.
- **Dams:** inflow today, 1 and 2 days ago, 7- and 30-day means, 7-day trend, 7-day mean release, storage %, 7-day
  storage change, the dam's average next-7-day inflow for this time of year (**training years only**), day of year.
  Inflow figures are divided by each dam's mean daily inflow in the training years, so big and small dams share one model.

Rain is past rain only: the model does not know tomorrow's forecast rain.

## Split (by time, no shuffling)
| | Rivers | Dams |
|---|---|---|
| Train | 2021-11-07 – 2024-11-04 (every 3rd hour: 1.28-1.45 M rows by horizon) | 2004 – 2022 (117,906 rows) |
| Validation (choices only) | 2024-11-05 – 2025-11-04 | 2023 – 2024 |
| **Test (reported)** | **2025-11-05 – 2026-11-05** | **2025-01-01 – 2026-11-04** |

A row is in a period only when its target is too: the last h hours of training and validation (7 days for dams)
are left out, so no target falls in the next period.

Chosen on validation only: absolute-error loss for both (rivers 24 h MAE 0.144 vs 0.162 m with squared error, on the
first cleaning; dams 8.9 vs 9.9-10.2 million m³), 600 trees for dams (2,000 did no better), and the trend baseline's
damping.

## Results on the test period (never used for fitting or choosing)

### Rivers: error of the level h hours ahead (MAE, m) and rises over the bank caught
| Horizon | Model | No change | Model, near the bank* | No change, near the bank* | Rises over the bank | Caught (recall) | Warnings that came true (precision) |
|---|---|---|---|---|---|---|---|
| +6 h | **0.080** | 0.111 | **0.112** | 0.151 | 1,733 | 24.2 % | 65.8 % |
| +12 h | **0.094** | 0.154 | **0.106** | 0.211 | 2,535 | 40.7 % | 76.6 % |
| +24 h | **0.121** | 0.140 | **0.153** | 0.208 | 2,853 | 15.3 % | 67.0 % |

\* within 1 m below the bank or above it — the hours that matter for a warning.

- Error is **28 % lower** than "no change" at +6 h, **39 %** at +12 h and **13 %** at +24 h; near the bank,
  **26 %**, **49 %** and **27 %**.
- "No change" and "trend" never warn of a rise over the bank. The model catches a quarter to two fifths of them
  6-12 hours ahead, and two out of three to three out of four of its warnings come true.

#### What rain added (same split, same settings; kept because validation MAE fell at every horizon)
On validation, +24 h recall fell (17.1 → 16.2 %) while precision rose (65.1 → 65.7 %); the test year is shown below.
| Horizon | MAE without → with rain (test, m) | Rises caught without → with rain (test) | Precision without → with rain (test) |
|---|---|---|---|
| +6 h | 0.0807 → 0.0798 | 24.7 % → 24.2 % (428 → 420 of 1,733) | 66.0 % → 65.8 % |
| +12 h | 0.0953 → 0.0939 | 37.0 % → 40.7 % (938 → 1,033 of 2,535) | 76.3 % → 76.6 % |
| +24 h | 0.1222 → 0.1211 | 15.7 % → 15.3 % (448 → 436 of 2,853) | 66.2 % → 67.0 % |

A small gain overall; the clearest one is 95 more rises over the bank caught 12 hours ahead. Most of the information is already in the river itself and its upstream stations; +24 h would need
**forecast** rain, which is not in this model.
- **Trend is worse than no change:** carrying the last 6 hours' rate forward raised the validation error at every
  damping (first cleaning: 6 h: 0.125 → 0.174 m at full trend; 24 h: 0.174 → 0.522 m), so its best setting is
  "no trend", again after the fix. Rivers turn too often for a straight line.
- **Per station** (+6 h): the model beat "no change" at 273 of 290 stations in the validation year; 266 of those
  273 still beat it in the test year (+12 h: 260 of 267; +24 h: 251 of 261). That list
  (`stations_better_on_valid` in the metrics) is where a forecast could be shown.

### Dams: inflow of the next 7 days (million m³), 35 dams
| Method | MAE | MAE, wettest 10 % of weeks | Mean bias |
|---|---|---|---|
| **Gradient boosting (this model)** | **10.7** | **74.1** | −4.1 |
| Last 7 days repeated | 14.1 | 96.1 | −0.1 |
| Climatology (same weeks, training years) | 14.6 | 91.7 | −3.8 |

24-26 % lower error than the baselines, 19-23 % in the wettest weeks. It aims at the median, so it is a little low on
average (−4.1 million m³ a week); a big week is still underestimated.

## Limits
- One year of test data per model. The test year includes the 2026 floods, but more seasons would make the
  numbers firmer.
- Catching 15-41 % of rises over the bank is better than the baselines' 0 %, but most rises still come without
  a warning. With past rain only, the model cannot see a storm that has not yet fallen.
- Rain in training is ERA5 (reanalysis). Live, the last few days would come from Open-Meteo's forecast model
  instead, which is not the same data; check the gap before trusting live forecasts.
- Station metadata (bank level) is today's; a bank that changed in the five years would shift `to_bank_m` for older rows.
- Not checked against RID's own forecasts.

## Next steps
1. **Forecast rain** for +24 h (e.g. Open-Meteo's historical forecast API, so training sees what was forecast
   at the time, not what fell).
2. **Serving:** before showing anything, add a function that builds today's features from the live 72-hour history,
   show the forecast only at `stations_better_on_valid`, label it as a model forecast, and keep the alert level from
   `rules.yaml`.
3. Retrain yearly with `make water-model`.

## Files
- `scripts/download_water_history.py`, `scripts/train_water_model.py`, `make water-model`
- `06_hazard_risk_models/risk_models/water/` (`download.py`, `dataset.py`, `model.py`), tests in `tests/test_water.py`
- Not in git: `data/raw/water_history/` (91 MB with rain), `data/processed/models/water_model.joblib` (10 MB)
- Metrics: `06_hazard_risk_models/reports/water_model_metrics.json`
