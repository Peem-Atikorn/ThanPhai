# Water-level and dam-inflow models — report (2026-11-05)

**Questions the models answer**
1. **Rivers:** how much will the water level at this station change in the next **6, 12 and 24 hours** (m)?
   From that, will water that is below the bank now be **over the bank** by then?
2. **Dams:** how much water will flow into this dam in the **next 7 days** (million m³)?

Neither model sets an alert level. Levels still come only from `rules.yaml`; nothing in the app uses these models yet.

## Data (ThaiWater, `scripts/download_water_history.py`, 94 min, 75 MB gzip, no failed request)
- **Rivers:** 311 stations with a trusted bank level and a flow gauge or RID key-station status (289 RID, 15 EGAT,
  6 HII, 1 DWR), grouped like the app's river view. Five years, 2021-11-06 to 2026-11-05, from
  `public/waterlevel_graph` in 90-day chunks. RID stations report about hourly, HII every 10 minutes, EGAT every 15.
  295 stations keep at least 60 days after cleaning; 8.2 million station-hours.
- **Dams:** all 35 large dams, daily inflow, release and storage from `analyst/dam_yearly_graph`.
  Used from 2004 to 2026-11-04 (199,142 dam-days).

### Cleaning (each found in this data)
| Problem | Example | Fix |
|---|---|---|
| Single spike | one reading 4 m off that comes straight back | dropped when > 1.5 m from both neighbours |
| Datum change | stations that move 20-30 m within an hour and stay there | only the readings after the last lasting step (> 3 m, the day after vs the day before) |
| Run of bad readings | Ct.4 sat 20 m low for 12 hours on 2023-04-04 | dropped when > 5 m from the median of the 49 hours around it |
| "No reading" marker | MKSND02 reports `9999.9` | values ≥ 999 dropped |
| Implausible bank | X.258: bank 18.4 m, water at ~51 m | readings outside bank −40 m … bank +15 m dropped |
| Dam running totals | 2002-2003 hold the total since 1 January (Bhumibol 3,946 → 4,005) | dam data from 2004 only; a day above 5 × the dam's 99th percentile is dropped |

After cleaning, the largest 24-hour change left in the river data is 6.5 m (a flash flood is possible; 20 m is not).
Years before 2018 have many missing dam days; rows whose 7-day target has a gap are not used.

## Features (data up to hour/day *t* only)
- **Rivers:** level change over the last 1, 3, 6, 12, 24, 48, 72 h; level minus bank; spread of the last 24 h;
  mean 6-h and 24-h change of the *other* stations in the same basin (upstream water shows up there first);
  day of year and hour (sin, cos). The target is the change, not the level, so one model serves every station.
- **Dams:** inflow today, 1 and 2 days ago, 7- and 30-day means, 7-day trend, 7-day mean release, storage %, 7-day
  storage change, the dam's average next-7-day inflow for this time of year (**training years only**), day of year.
  Inflow figures are divided by each dam's mean daily inflow in the training years, so big and small dams share one model.

**No rain yet.** ThaiWater has no rain history endpoint (`rain_graph` → 404), and five years of hourly rain from
Open-Meteo for the stations' 1° cells would use about 6,800 of the 10,000 weighted calls a day that the live app's
heat and rain-chance forecasts also use from the same machine. See "Next steps".

## Split (by time, no shuffling)
| | Rivers | Dams |
|---|---|---|
| Train | 2021-11-07 – 2024-11-04 (every 3rd hour: 1.44 M rows) | 2004 – 2022 (118,127 rows) |
| Validation (choices only) | 2024-11-05 – 2025-11-04 | 2023 – 2024 |
| **Test (reported)** | **2025-11-05 – 2026-11-05** | **2025-01-01 – 2026-11-04** |

Chosen on validation only: absolute-error loss for both (rivers 24 h MAE 0.144 vs 0.162 m with squared error;
dams 8.9 vs 9.9-10.2 million m³), 600 trees for dams (2,000 did no better), and the trend baseline's damping.

## Results on the test period (never used for fitting or choosing)

### Rivers: error of the level h hours ahead (MAE, m) and rises over the bank caught
| Horizon | Model | No change | Model, near the bank* | No change, near the bank* | Rises over the bank | Caught (recall) | Warnings that came true (precision) |
|---|---|---|---|---|---|---|---|
| +6 h | **0.076** | 0.105 | **0.113** | 0.151 | 1,741 | 23.5 % | 66.8 % |
| +12 h | **0.093** | 0.141 | **0.119** | 0.211 | 2,549 | 31.3 % | 75.1 % |
| +24 h | **0.125** | 0.142 | **0.160** | 0.210 | 2,899 | 15.1 % | 64.7 % |

\* within 1 m below the bank or above it — the hours that matter for a warning.

- Error is **28 % lower** than "no change" at +6 h, **34 %** at +12 h and **12 %** at +24 h; near the bank,
  **25 %**, **44 %** and **24 %**.
- "No change" and "trend" never warn of a rise over the bank. The model catches a quarter to a third of them
  6-12 hours ahead, and about two out of three of its warnings come true.
- **Trend is worse than no change:** carrying the last 6 hours' rate forward raised the validation error at every
  damping (6 h: 0.125 → 0.174 m at full trend; 24 h: 0.174 → 0.522 m), so its best setting is "no trend". Rivers turn
  too often for a straight line.
- **Per station** (+6 h): the model beat "no change" at 261 of 279 stations in the validation year; 256 of those
  261 still beat it in the test year (+12 h: 254 of 263; +24 h: 237 of 247). That list
  (`stations_better_on_valid` in the metrics) is where a forecast could be shown.

### Dams: inflow of the next 7 days (million m³), 35 dams
| Method | MAE | MAE, wettest 10 % of weeks | Mean bias |
|---|---|---|---|
| **Gradient boosting (this model)** | **10.8** | **74.1** | −4.1 |
| Last 7 days repeated | 14.1 | 96.1 | −0.1 |
| Climatology (same weeks, training years) | 14.6 | 91.6 | −3.9 |

24 % lower error than either baseline, 19-23 % in the wettest weeks. It aims at the median, so it is a little low on
average (−4.1 million m³ a week); a big week is still underestimated.

## Limits
- One year of test data per model. The test year includes the 2026 floods, but more seasons would make the
  numbers firmer.
- Catching 15-31 % of rises over the bank is better than the baselines' 0 %, but most rises still come without
  a warning. Without rain the model cannot see a storm that has not yet raised any river.
- Station metadata (bank level) is today's; a bank that changed in the five years would shift `to_bank_m` for older rows.
- Not checked against RID's own forecasts.

## Next steps
1. **Rain:** fetch hourly rain once (Open-Meteo archive in batches across two days to leave quota for the live app,
   or CHIRPS/IMERG files) and set `--rain`; the rain features are already in the dataset code.
2. **Serving:** before showing anything, add a function that builds today's features from the live 72-hour history,
   show the forecast only at `stations_better_on_valid`, label it as a model forecast, and keep the alert level from
   `rules.yaml`.
3. Retrain yearly with `make water-model`.

## Files
- `scripts/download_water_history.py`, `scripts/train_water_model.py`, `make water-model`
- `06_hazard_risk_models/risk_models/water/` (`download.py`, `dataset.py`, `model.py`), tests in `tests/test_water.py`
- Not in git: `data/raw/water_history/` (75 MB), `data/processed/water_river_table.parquet` (363 MB, a cache),
  `data/processed/models/water_model.joblib` (10 MB)
- Metrics: `06_hazard_risk_models/reports/water_model_metrics.json`
