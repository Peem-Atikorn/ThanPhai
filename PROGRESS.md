# Progress — ThanPhai

Status: **round 5 complete** (2026-10-19 15:00). All keys in use (TMD NWP, FIRMS, GISTDA, Earthdata, Gemini) except LINE and map keys.
Everything runs in Docker; 240 tests pass (232 offline + 8 live).

## Running now
`docker compose up -d --build` → all healthy:

| Service | Port | What |
|---|---|---|
| web (01) | 3000 (LAN) | Next.js + MapLibre map of 928 districts, hazard tabs, area card, question box |
| api-backend (02) | 127.0.0.1:8002 | Gateway; runs 03 agent, 05 geo, 06 fire model, 07 rules + Gemini in-process. Live data every 15 min, TMD NWP forecast 3 h, GISTDA satellite 6 h, fire model 3 h |
| external-data (04) | (off by default) | `docker compose --profile standalone up` — api-backend uses module 04 as a library |
| postgis | 127.0.0.1:5432 | `admin_area` table (77 + 928 + 7,425 areas, GIST index) |
| redis | 127.0.0.1:6379 | Ready for cache / queues |

## Done
- [x] Planning files 01–08; `docs/API_REQUIREMENTS.md` (verified live); `docs/DATA_SOURCES.md`
- [x] `shared/` — Pydantic schemas
- [x] **01 Web** — Next.js 16 + MapLibre 6 + OSM basemap (no key); hazard tabs, legend with counts, area card with reasons and sources,
      question box, TMD warning panel, emergency numbers, data time; `/api/*` proxied to module 02
- [x] **02 API** — `/v1/areas/search`, `/v1/areas/{code}/hazards` (tambon → its district, province → worst district),
      `/v1/map/layers/{hazard}` (GeoJSON), `/v1/warnings`, `/v1/sources`, `/v1/ask`, request IDs, degraded-service reporting
- [x] **03 Agent** — deterministic: intent (risk / explain / prepare / emergency / out of scope), hazard, area from free Thai/English text,
      time window; Thai answers with reasons, TMD warning, sources, emergency numbers. LLM optional later.
- [x] **04 External data** — Open-Meteo (forecast, GloFAS discharge), ThaiWater (rain, water level), NASA FIRMS, TMD (warnings v2, 3-hour obs)
- [x] **05 Geo** — HDX boundaries → GeoPackage + gazetteer + PostGIS; AreaMapper (provider code → polygon → nearest ≤2 km); flood/rain/fire features
- [x] **06 Risk** — SPI drought index (gamma, McKee 1993) with ERA5 history and cache
- [x] **07 Decision** — versioned `rules.yaml`, official warning floor, safety overrides, "no data" instead of "normal"
- [x] **08 Alerts** — alert policy: raise/lower, minimum level, cooldown against flapping, quiet hours, CRITICAL bypass, one message per user
- [x] Docker: Dockerfiles for 01, 02, 04; compose with web + api + external-data + postgis + redis
- [x] Scripts: `build_admin_areas.py`, `load_postgis.py`, `live_area_snapshot.py`, `live_drought_snapshot.py`, `train_fire_model.py`, `download_firms_history.py`, `eval_gemini.py`, `start-docker.ps1`

## Test results (2026-10-19 15:00)
| Suite | Result |
|---|---|
| shared (redaction) | 4 passed |
| 02 API | 25 passed (refresh, backoff, layer cache, static sources) |
| 03 Agent | 32 passed (forecast days, satellite reason, verbatim safety lines) |
| 04 External data | 32 offline + 8 live passed (TMD NWP, GISTDA, rate limits) |
| 05 Geo | 45 passed (real HDX data) |
| 06 Risk | 27 passed (SPI, fire dataset, thresholds, static mask) |
| 07 Decision + Gemini explainer | 65 passed |
| 08 Alerts | 11 passed |
| 01 Web | `tsc`, `eslint`, `next build` clean; checked in a browser (desktop + mobile) against Docker |
| Gemini live eval | `scripts/eval_gemini.py`: 9/12 accepted before checker fixes; rejections fall back to the rule answer |
| Fire model | test year 2025: PR-AUC 0.729 vs 0.391 for the old rule; recall 0.85 vs 0.19 — `06_hazard_risk_models/reports/fire_model.md` |

## Bugs found and fixed
Round 1:
1. **TMD warnings v1 is stale** — latest item 2022-10-16 while answering 200 OK. Switched to v2.
2. **ThaiWater `diff_wl_bank` is always positive** — direction is only in the Thai text; 774 stations below their bank would read as overflowing.
3. **ThaiWater stations without a bank** (`min_bank = 0`) report the MSL level as overflow ("+331 m"). Now unknown, with a 10 m plausibility guard.
4. **Freshness used forecast `valid_time`** — a 3-day-old forecast would look fresh. Now `issued_at`.
5. **NORMAL flood from rain data alone** — only hazard-relevant variables count; otherwise "no data".
6. **SPI used a partial last month** (Khon Kaen −0.71 instead of +0.39). Incomplete months are dropped.
7. **Open-Meteo 429** — history is weighted (~900 calls per 35-year request). Cache + retry; CHIRPS for bulk history.

Round 2:
8. **Docker Desktop crashed** on stale AF_UNIX socket files in `%LOCALAPPDATA%\Docker\run` that could not be renamed or deleted.
   Renamed the folder to `run.broken-20261019` (nothing deleted); Docker recreated it and starts normally.
9. **Border-river stations** mapped to the wrong province (24/802). ThaiWater's registered tambon code now wins → 800/802 agree.
10. **"อำเภอเสนาไหม" → ต.นาไหม** (Udon Thani): the particle "ไหม" glued onto the name. Prefix matches win; particle-swallowing matches rank last.
11. **Same-name tambon beat its district** (หาดใหญ่). The larger area wins without a prefix.
12. **Aliases and hyphens**: โคราช / อยุธยา / กรุงเทพ / สุไหงโกลก found nothing; a real tambon named โคราช beat the province.
13. **Short names**: ตาก ignored, while "เลย" (Loei / "at all") must not match mid-sentence. Short names only after a prefix or at the start.
14. **"Hat Yai rain" → ต.ไร่**: "rai" inside "rain". English names now need word boundaries; best match first.
15. **50 of 141 FIRMS hotspots** in the Thailand bbox are in neighbouring countries — removed by polygon mapping.
16. **MapLibre v6 worker failed under Turbopack** (blank map). Worker copied to `public/` on `npm install`, `setWorkerUrl` set.
17. **Docker `env_file` read a comment as the value**: `FIRMS_MAP_KEY=   # optional` sent FIRMS a URL containing the comment (400).
    `.env.example` keeps comments on their own lines now.

Round 3 (review fixes, 2026-10-19 03:20):
18. **Future questions answered with current data** without saying so ("พรุ่งนี้ฝนตกไหม"). The agent now detects future questions
    (time words, "จะ", "will") and states that no forecast exists yet; current-time words ("ตอนนี้") override.
19. **"พรุ่งนี้" computed on the UTC clock** — wrong date between 00:00 and 07:00 Thai time. The agent converts to Asia/Bangkok first.
20. **First request after the 15-minute TTL waited ~10 s** for every provider. Now stale-while-revalidate: old data is served at once
    (with `data_updated_at`), one background refresh runs; a failed refresh keeps the old data; failed TMD keeps the last warnings.
21. **Refresh blocked the event loop** (mapping ~5,000 points + 2,784 decisions). Processing now runs in a worker thread and swaps results atomically.
    The first version of the test passed even with blocking code; rewritten with a ticker and checked against a deliberately blocking build.
22. **PostGIS, Redis, API, and external-data ports were open on every interface.** Bound to 127.0.0.1; only the web (3000) stays on the LAN, on purpose.

Round 4 (Gemini, 2026-10-19 13:00):
23. **LLM = Gemini** (`gemini-3.8-flash`, thinking LOW) via `google-genai` `generate_content`. The explainer only rewrites the
    rule-based answer; it is rejected (template shown instead) if it adds or drops an alert level, adds a number, or drops the
    emergency numbers or the no-forecast notice. Off when `GEMINI_API_KEY` is empty. The `/v1/ask` call runs in a thread.
    Web shows "เรียบเรียงโดย AI (Gemini)" when used. Live Gemini call not tested yet (no key).
24. **Zero FIRMS hotspots crashed the whole refresh** (seen live: rainy season, 0 hotspots over Thailand) → empty frame without
    columns → KeyError → no data → HTTP 500 on every request. Empty frames keep their columns; one hazard failing now degrades
    only that hazard; a failed first refresh returns 503, not 500.
25. **Docker stopped after Windows shut down / slept** (exit 0x40010004) and left a stale socket again. `scripts/start-docker.ps1`
    moves stale socket files aside, starts Docker, waits, and runs `docker compose up -d`. (Its first version failed on
    PowerShell 5.1, which turns docker's stderr into an error under `Stop`; fixed by probing through cmd.)
26. **eslint linted the vendored MapLibre worker** (1,103 warnings). Ignored in `eslint.config.mjs`.

Round 5 (all keys, 2026-10-19 13:00–15:00):
27. LLM model switched to `gemini-3.5-flash-lite` (user choice). Live: 1.2–2.9 s per answer.
28. **Checker rejected correct answers**: "ระดับเตือนภัยตัดสินด้วยกฎ" read as a level; several hazards on one line; "Flooding"
    not matched; numbers from the question ("3 วัน"); Buddhist year written as 2026. Levels are now attached to the hazard named before them.
29. **Gemini free-tier 429** after ~24 calls/minute: answer cache, 60 s pause, at most 4 calls at once.
30. **TMD NWP**: 3-day forecast for 77 provinces + 928 districts (77 calls). Live 429 on 17 provinces (limit 60/min) → honours `retry-after`.
    "พรุ่งนี้" questions now answer with the forecast; flood/fire still say there is no forecast.
31. **GISTDA**: satellite flood cells per district by counting (`numberMatched`), ~400 calls / 17 s, total matched exactly (80,387).
    New rules SAT_FLOOD_7D (WATCH) and SAT_FLOOD_7D_LARGE (WARNING); flood "no data" districts dropped from 530 to 0.
    GISTDA echoes the API key in response links — never stored or returned.
32. **Fire model trained** on FIRMS 2021–2025 (1.55 M detections). Threshold search took 8 min → precision_recall_curve (same result, 2 s).
33. **Cement kilns and refineries predicted as wildfire** (Saraburi, Si Racha). FIRMS type-2 points removed; 74 static cells masked in live data.
34. **FIRMS key in container logs** (httpx INFO logs full URLs) → `thanphai_shared.redact` on all logs and error strings; verified 0 leaks.
35. **Map layer 2.7 MB** (15-digit coordinates): rounded to 4 decimals + gzip → 275 KB.
36. **"Data time: —"** after satellite/fire updates (updated_at was cleared) → separate inputs-changed flag, reset before the snapshot.
37. Mobile layout: hazard tabs wrapped over the zoom buttons; legend covered half the map → one scrolling row, compact legend.
38. Earthdata token slot added (`EARTHDATA_TOKEN`); token valid until 2026-12-18.

Audit (ponytail-audit) fixes:
39. Failing slow jobs restarted on every request (77/400 provider calls each) → one job runner with 10-minute backoff.
40. Thai place search took 0.8 s per question (12,308 regexes) → substring pre-check, 1.4 ms.
41. Map layer: JSON encoding 285 ms + gzip 140 ms on the event loop per request → encoded and compressed once per data version (3 ms).
42. Gemini could echo a number from the question as a measurement ("สูง 3 เมตรใช่ไหม") → only time-window numbers allowed.
43. Gemini could invent advice → it writes only the summary; warnings, emergency numbers and sources are appended verbatim; advice words rejected.
44. One slow Gemini could take every worker thread → max 4 concurrent calls, template otherwise.
45. One failing GISTDA province lost the whole country → per-province errors, districts left unknown (not 0).
46. Clicking two districts fast could show the wrong card → only the latest request fills it.
47. external-data container off by default (unused, uncached); `scripts/live_flood_snapshot.py` removed (replaced by live_area_snapshot).

Round 6 (NDVI drought, 2026-10-19):
48. **NDVI now used**: MODIS MOD13A3 (monthly 1 km, 5 tiles, 55 files / 648 MB) → district NDVI → VCI for 2026-09
    against September 2016–2025 (`scripts/build_ndvi_vci.py`, `make ndvi`). Drought map tab enabled.
    Harmony subsetting is not available for MODIS NDVI, so whole tiles are downloaded; pyhdf reads HDF4
    (rasterio's GDAL has no HDF4 driver). District grid built by rasterising boundaries onto the MODIS sinusoidal grid.
49. **Raw VCI would have raised 121 false drought alerts**: 35 % were flooded districts (water lowers NDVI),
    urban/industrial districts read VCI 0 (บางพลี, เมืองระยอง), and green rainy-season districts read VCI 0 from a
    0.001 drop (range only 0.03–0.1). Now: VCI counts only if NDVI is ≥ 0.05 below normal, the district is not
    mostly urban (NDVI max ≥ 0.6) and no flood was seen; vegetation alone gives at most WATCH (higher levels need SPI).
    Result for 2026-09: NORMAL 698, WATCH 3, no data 227 (floods 162, urban, cloud).

Round 7 (dams, 2026-10-19):
50. **Dam storage added**: 35 large dams from ThaiWater `thailand_main` (refreshed with live data every 15 min).
    Status uses ThaiWater's own rule: storage > 80 % of normal level = น้ำมาก, usable water ≤ 30 % = น้ำน้อยวิกฤต.
    API `GET /v1/dams`; area card lists dams in the province; sidebar panel with bars, click to zoom; agent answers
    "เขื่อนภูมิพลมีน้ำเท่าไหร่" / province / nationwide straight from the data (never rewritten by the LLM).
51. **"เขื่อน" is also a tambon name** (ต.เขื่อน, มหาสารคาม): every dam question resolved to it → ignored in dam questions.
52. The agency in dam data is the reporter (often RID, ชป.), not the owner (ภูมิพล is EGAT's) → shown as "รายงานโดย".

### Round 8 — dam category
53. Dam status labels reworded the way people talk ("น้ำมาก" → "น้ำใกล้เต็มอ่าง" / "น้ำเกินระดับกักเก็บปกติ"; "น้ำน้อยวิกฤต" → "น้ำใช้การได้เหลือน้อย").
54. Web: dams moved into their own 5th tab "เขื่อน". The map shows dam points (size = water in reservoir, colour = status) with a status legend and today's total release.
    The list sorts by storage or by today's release. The detail card shows release, inflow and spill (million m³ and average m³/s), storage vs normal/max, usable %, totals since 1 January (% of the average yearly inflow), basin, reporter and date.
    The always-visible dam panel and the area-card dam list were removed.
55. Agent: answers "เขื่อนไหนระบายน้ำมากที่สุด" (top 5 by release, plus the national total) and adds m³/s to every flow. The m³/s figure is the daily volume ÷ 86,400 (a daily average, not the current gate rate), and the UI says so.
56. Fixed: basin names already start with "ลุ่มน้ำ" — no double prefix.

### Round 9 — rivers, weather images, dam history
57. River view (6th tab "แม่น้ำ"): hourly flow (m³/s), % of channel capacity (qmax), level vs bank and trend for ~300-470 stations, merged from duplicate ThaiWater rows (`C.13` + `ridhydro_C.13`).
    Lists: Chao Phraya main stem upstream→downstream (RID's order), over-bank stations, top flow. Detail card with 3-day hourly graphs (level vs bank, flow vs capacity). River status uses the flood rule thresholds, so both views agree (checked live: every over-bank station's district is WARNING+).
58. Dam detail: this year vs last year (storage, daily release, daily inflow) with the upper/lower rule-curve band (ThaiWater `analyst/dam_yearly_graph`).
59. Rain/storm tab: radar (fresh ≤3 h only; 21-23 of 38), HII rain forecast (Thailand, basin, SE Asia, Asia; 3-7 days), storm track maps (W. Pacific shows CHAMPI), SWAN wave forecast.
60. Agent: river questions ("แม่น้ำเจ้าพระยา…", station codes, "น้ำล้นตลิ่งที่ไหน"); "เขื่อนเจ้าพระยาระบายเท่าไหร่" answers with the flow at C.13 (diversion dam, not one of the 35).
61. API: `/v1/rivers`, `/v1/rivers/{id}/graph`, `/v1/dams/{id}/history`, `/v1/media`; graph/history only for known ids (no open proxy), 15 min / 6 h cache.
62. Bug: water-level readings older than 6 h kept driving flood levels (BKC002 Samut Prakan, last reading the day before, "over the bank"). Now dropped; all-stale feed = degraded, not "normal".
63. Bug: BMA radar rows say timezone "TST" but are UTC (image stamp 17:55 vs field 10:55) — trusting the label hid the newest Bangkok radar.
64. Bug: "แม่น้ำเจ้าพระยา" resolved to ต.แม่น้ำ (Ko Samui); river words count as a place only when written "ต./ตำบล…".
65. Bug: station "C.3" matched inside "C.35"; codes now match whole only. "pm2.5" is not a station code.
66. UI: selecting a dam/station scrolls its card into view when it is off screen; toolbar wraps to 3×2 on phones.
67. Searched province or sub-district is outlined on the map (white casing + dark line) and the map fits to it; `GET /v1/areas/{code}/boundary` (code must be in the gazetteer; outlines cached).
68. Bug: after a backend restart the first map layer was built before satellite data arrived (540 districts "no data") and the page kept it, so the province card (loaded later) disagreed with the map colours.
    Now `meta.inputs_pending` lists slow inputs still loading; the page re-polls every 30 s until empty, then every 10 min (visible tab only).
69. Weather images were hard to find (only under the rain tab, below the map): "เรดาร์ · คาดการณ์ฝน · พายุ" button in every hazard map heading and a link under the area's rain forecast jump to them.
70. River view opened from a province card shows that province only (list, map points, legend counts, outline + fit), with a "ดูทั้งประเทศ" chip; the card link shows for every province with stations, not only when one overflows.
71. API-hammering check: idle page = 3 requests per load (layer, dams, rivers), then nothing until the 10-min refresh. On-demand graphs/histories now share one upstream call per key while loading and remember a ThaiWater failure for 60 s (repeated clicks no longer re-call it).
72. Bug: each auto-refresh blanked the map for a moment (layer reset to null while loading); the last layer of the same hazard stays until the new one arrives.

## Notes
- Pressing Enter in the question box did not submit when text was typed by the browser automation tool; clicking "ถาม" works.
  Check with a real Thai keyboard (IME composition can swallow Enter).
- TMD warnings are national; they are shown as context but not applied per district until warning text can be mapped to provinces.
- Drought uses NDVI/VCI (monthly, ~2–6 weeks behind). Rainfall-deficit SPI per district still needs a CHIRPS backfill.
- Earthdata token expires 2026-12-18; `make ndvi` fails with "not an HDF file" after that until it is renewed.
- Gemini free tier: Google may use requests to improve its products. Move to the paid tier before real users.
- After a container restart, satellite and forecast data take ~20–80 s to load; until then those inputs are missing.

## Blocked — needs the team
- [x] Keys: TMD NWP, FIRMS, GISTDA, Earthdata, Gemini — all verified live
- [ ] LINE channel (alerts), map key (Sphere or Longdo, optional)
- [x] LLM provider: Gemini `gemini-3.5-flash-lite` — live, validated, with template fallback
- [ ] Collect DDPM guide PDFs for the RAG knowledge base (module 07)

## Next steps
- [ ] Module 06: CHIRPS backfill → SPI per district (lets drought reach WARNING/CRITICAL); NDVI + TMD humidity as fire-model features; flood model from GISTDA flood history
- [ ] Run `make ndvi` monthly (or schedule it)
- [ ] Module 07: RAG over DDPM guides (needs PDFs)
- [ ] Water quality (42 stations) and active-storm list (`storm_data`) when a storm is near Thailand
- [ ] Module 05: map TMD warning text to provinces so warnings can set per-district minimum levels
- [ ] Module 08: LINE delivery + subscription storage (needs LINE channel); ground-report endpoint
- [ ] Split 03/05/07 into their own services when the team is ready (only `hazard_service.py` changes)
- [ ] Keycloak auth (module 02)
