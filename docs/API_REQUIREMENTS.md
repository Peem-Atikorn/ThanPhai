# API Requirements — ThanPhai (ทันภัย)

Every external API the system needs: what it gives us, how to get access, limits, license, and which module uses it.

**Verification legend**
- ✅ **Live-tested** — called from this project on 2026-10-19 and got real data
- 🔑 **Needs key** — endpoint confirmed (returns an auth error without a key); the team must register
- 📄 **Docs only** — read from the provider's documentation or third-party reports; not called yet
- ⚠️ **Check** — terms, quota, or pricing must be re-checked by the team before release

---

## 0. Summary — what the team must do first

| Priority | Action | Who applies | Cost | Needed by |
|---|---|---|---|---|
| 1 | Register a **TMD NWP API** account and create a Personal Access Token | 1 person, shared via `.env` | Free | 04, 06 |
| 2 | Register a **NASA FIRMS MAP_KEY** (email only) | 1 person | Free | 04, 06 |
| 3 | Register a **GISTDA API Gateway** key (flood extent, hotspots) | 1 person | Free (gov open data) | 04, 06 |
| 3b | Create a **NASA Earthdata Login** account and generate a user token (NDVI) | 1 person; renew every 60 days | Free | 06 |
| 4 | Create a **LINE Official Account** + Messaging API channel | Module 08 owner | Free plan: 300 push msgs/month | 08 |
| 5 | Get a **map key**: GISTDA Sphere *or* Longdo Map (or use OSM / MapLibre with no key) | Module 01 owner | Free tier | 01 |
| 6 | Get a **Gemini API key** (Google AI Studio) | Team | Free tier available; paid per token | 03, 07 |
| — | Everything else in this file works **without a key** | — | Free | — |

> Never commit keys. Put them in `.env` (git-ignored) and keep only placeholders in `.env.example`.

---

## 1. Hazard data APIs

### 1.1 TMD Open Data API — กรมอุตุนิยมวิทยา ✅

| Item | Detail |
|---|---|
| Base URL | `https://data.tmd.go.th/api/<Service>/<version>/?uid=<uid>&ukey=<ukey>&format=json` |
| Auth | `uid` + `ukey` query parameters. TMD publishes a demo pair (`uid=api`, `ukey=api12345`) that works for open services. Register your own at <https://data.tmd.go.th> for production. |
| Format | XML (default) or JSON with `format=json` |
| License | Thai Meteorological Department open data — show "ที่มา: กรมอุตุนิยมวิทยา" ⚠️ |
| Used by | 04 (warnings, observations), 06 (rain/storm, fire features), 07 (official warnings) |

| Service | Endpoint | Data | Status |
|---|---|---|---|
| Weather warning news | `/api/WeatherWarningNews/v2/` | Official warnings: `IssueNo`, `EffectStartDate`, `EffectEndDate`, `AnnounceDate`, `TitleThai`, `HeadlineThai`, `WebUrlThai` (PDF). `Warning` is an object when there is one warning and a list when there are several. | ✅ |
| ~~Weather warning news v1~~ | `/api/WeatherWarningNews/v1/` | **Do not use** — still answers 200 OK, but its latest item is from 2022-10-16 (stale) | ⚠️ |
| 3-hour observations | `/api/Weather3Hours/V2/` | Station obs every 3 h (temp, humidity, rain, wind, pressure). Real-time observations only; no historical archive. | ✅ |
| 7-day forecast | `/api/WeatherForecast7Days/v2/` | Province-level 7-day forecast | ✅ |
| Daily seismic events | `/api/DailySeismicEvent/v1/` | Earthquakes (out of scope, but available) | ✅ |

> **Note on planned fire-model humidity feature (Module 06):**
> Training and live inference must use the same underlying data source to prevent train/serve skew (e.g. ERA5-Land reanalysis vs NWP forecast vs surface stations exhibit distinct biases and spatial distributions).
> Planned approach: Use Open-Meteo with matching NWP models on both sides: Historical Forecast API (`historical-forecast-api.open-meteo.com`) for training backfill and Forecast API (`api.open-meteo.com`) for live inference (using matching `models=` parameter, verifying historical model coverage back to 2021). Alternatively, if TMD surface stations are used live, the model must be trained on TMD historical surface station archives with consistent spatial interpolation (nearest station or IDW).

### 1.2 TMD NWP API (WRF model forecast) 🔑

| Item | Detail |
|---|---|
| Base URL | `https://data.tmd.go.th/nwpapi/v1/forecast/location/{hourly\|daily}/{at\|place\|region}` |
| Docs | <https://data.tmd.go.th/nwpapi/doc/> |
| Auth | `Authorization: Bearer <token>` — register, log in, then **Personal Access Tokens → Create New Token** |
| Data | Hourly / daily forecast at a lat-lon or place: rain, temperature, humidity, wind, cloud, condition. Hourly endpoint returns up to 48 h. |
| Status | Endpoint confirmed — returns `401 {"error":"Unauthenticated."}` without a token |
| Used by | 04, 06 (main rain/storm input) |
| Fallback | Open-Meteo forecast (1.6) |

### 1.3 ThaiWater API v3 — สสน. (HII) ✅

| Item | Detail |
|---|---|
| Base URL | `https://api-v3.thaiwater.net/api/v1/thaiwater30/public/` |
| Auth | None for public endpoints |
| Format | JSON (Thai / English / Japanese labels) |
| License | Data from HII and partner agencies — credit "ที่มา: คลังข้อมูลน้ำแห่งชาติ (สสน.)" ⚠️ check reuse terms |
| Used by | 04, 05, 06 (flood and drought) |

| Endpoint | Data | Size | Status |
|---|---|---|---|
| `waterlevel_load` | Latest water level for all telemetry stations: `waterlevel_msl`, `storage_percent`, `situation_level`, agency, geocode, and river flow (m³/s) in `discharge` **or** `flow_rate` with channel capacity `station.qmax`. Many RID stations appear twice (`C.13` and `ridhydro_C.13`); merge them by code. The row count varies by hour (803–1,399 seen) | ~1.4 MB | ✅ used (flood + river view) |
| `rain_24h` | 24-hour rainfall per station with province/amphoe/tambon geocode | ~4.7 MB | ✅ |
| `thailand_main` | Dashboard bundle. ThanPhai uses `dam` (35 large dams: storage, % of normal level, usable %, inflow, release, totals since 1 Jan; rule: >80 % high, usable ≤30 % critically low) and the images: `radar` (38 radars, many stale since 2017-2025, times in UTC even where `timezone` says TST), `pre_rain*` (HII WRF-ROMS rain forecast, 3-7 days), `storm` (track maps per ocean, Himawari), `wave` (SWAN, 7 days). Animations are 16-26 MB — not used | ~12 MB | ✅ used |
| `waterlevel_graph?station_type=tele_waterlevel&station_id=<id>&start_date=YYYY-MM-DD&end_date=YYYY-MM-DD` | Hourly level + `discharge` for one station, with `min_bank` and `qmax`. Without `station_type` and dates the server panics (500) | ~35 KB | ✅ used |
| `shared/image?image=<media_path>` (on `/thaiwater30/`, not `/public/`) | Serves any `media_path` token from `thailand_main` (jpg, png, gif, mp4) | 5 KB-26 MB | ✅ used |
| `../analyst/dam_yearly_graph?data_type=dam_storage\|dam_released\|dam_inflow&dam_id=<id>&year=2025,2026` | One value per day for whole years, plus `upper_rule_curve` / `lower_rule_curve` (sometimes as a Python-list string) | ~60 KB | ✅ used |
| `storm_data` | Active tropical storms with category scale; empty when there is none | small | ⏳ not used yet |

Notes: payloads are large — cache them and pull every 15–30 min, not per user request. `dam_daily` does **not** exist (404); dam data comes from `thailand_main`.

RID hourly report (`hyd-app.rid.go.th/hydro5h.html`): the Chao Phraya main stem hour by hour (P.17, N.67, C.2, C.13, C.3, C.7A, C.35, C.36, C.29B …).
It is an internal ASP.NET jqGrid service (`webservice/getGroupHourlyWaterLevelReportAllHL5.ashx`, POST, undocumented).
The same RID stations and values (C.13 = 1,800 m³/s on 2026-10-19 18:00 in both) are in ThaiWater `waterlevel_load`, so ThanPhai uses ThaiWater and links to the RID page as the reference.

### 1.4 NASA FIRMS — active fire hotspots ✅ / 🔑

| Item | Detail |
|---|---|
| Keyless files | `https://firms.modaps.eosdis.nasa.gov/data/active_fire/suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_SouthEast_Asia_24h.csv` (also `_48h`, `_7d`) ✅ |
| Area API | `https://firms.modaps.eosdis.nasa.gov/api/area/csv/{MAP_KEY}/{SOURCE}/{west,south,east,north}/{DAY_RANGE}[/{DATE}]` 🔑 |
| Sources | `VIIRS_SNPP_NRT`, `VIIRS_NOAA20_NRT`, `VIIRS_NOAA21_NRT`, `MODIS_NRT` (+ `_SP` archive for training) |
| Auth | Free MAP_KEY by email at <https://firms.modaps.eosdis.nasa.gov/api/map_key/> |
| Limit | 5,000 transactions per 10 minutes per MAP_KEY |
| Fields | `latitude, longitude, bright_ti4, frp, confidence, acq_date, acq_time, daynight` |
| License | NASA open data — credit "NASA FIRMS" |
| Used by | 04, 06 (fire), 06 labels (fire history) |

The keyless South-East Asia file (~1.8 MB/24 h) is enough for the MVP. Filter to the Thailand bbox `97.3,5.6,105.7,20.5` and then to the country polygon. The MAP_KEY is needed for historical backfill.

### 1.5 GISTDA API Gateway — flood extent and hotspots 🔑

| Item | Detail |
|---|---|
| Base URL | `https://api-gateway.gistda.or.th/api/2.0/resources/features/...` (OGC API – Features) |
| Examples | `.../flood/1day`, `.../flood/3days`, `.../flood/7days`, `.../flood/30days`, `.../viirs/1day` |
| Auth | Header `API-Key: <key>` — register at <https://api-gateway.gistda.or.th> |
| Status | Endpoint confirmed — returns `407 Authentication Required` without a key |
| Open data catalogue | <https://opendata.gistda.or.th/dataset/flood-disaster-data> ✅ (CKAN API works, also lists TMS / WMS / WMTS layers) |
| License | Thai government open data ⚠️ check dataset licence field |
| Used by | 04, 06 (flood labels and current flood area), 01 (map layer) |

### 1.6 Open-Meteo — forecast, history, river discharge ✅

| Endpoint | Data | Status |
|---|---|---|
| `https://api.open-meteo.com/v1/forecast` | Hourly / daily forecast (precipitation, humidity, wind, temperature) for any lat-lon | ✅ |
| `https://archive-api.open-meteo.com/v1/archive` | ERA5 / ERA5-Land history since 1940 — training data, SPI, soil moisture | ✅ |
| `https://flood-api.open-meteo.com/v1/flood` | GloFAS river discharge forecast (m³/s) | ✅ |

| Item | Detail |
|---|---|
| Auth | None |
| Limits (free) | 600 calls/min, 5,000/hour, 10,000/day. **Calls are weighted:** every 2 weeks of data (or more than 10 variables) counts as an extra call, so one 35-year daily history request costs ~900 calls. Hit in testing on 2026-10-19: `429 {"reason":"Minutely API request limit exceeded. Please try again in one minute."}` after 4–5 history requests. |
| Design rule | Fetch history **once** and cache it (`data/cache/`). Refresh only recent months. For history of all 77 provinces, use **CHIRPS** files (1.9) instead of Open-Meteo. Retry 429 after 65 s. |
| Terms | Free for **non-commercial** use (student project qualifies). Commercial use needs a paid plan. |
| License | CC BY 4.0 — credit "Weather data by Open-Meteo.com" |
| Used by | 04 (fallback forecast), 05/06 (history and features), 06 (flood: river discharge), 02 (heat index: hourly temperature + humidity per 0.4° cell every 3 h, ~306 calls) |

### 1.7 DDPM — กรมป้องกันและบรรเทาสาธารณภัย 📄

| Item | Detail |
|---|---|
| Data | Declared disaster areas, situation reports, preparedness guides |
| Access | No public REST API found. Use datasets on data.go.th and PDF guides from <https://www.disaster.go.th> |
| Used by | 06 (labels: declared drought / flood areas), 07 (RAG documents) |
| Action | Download and version the guides manually; record the source URL and date for each file |

### 1.8 data.go.th (CKAN) ✅

| Item | Detail |
|---|---|
| API | `https://data.go.th/api/3/action/package_search?q=<keyword>` |
| Auth | None for search; some datasets point to provider APIs that need their own keys |
| Data | 370+ drought-related datasets, admin boundaries, historical disaster statistics |
| Used by | 05 (reference data), 06 (labels) |

### 1.9 CHIRPS rainfall ✅

| Item | Detail |
|---|---|
| URL | `https://data.chc.ucsb.edu/products/CHIRPS-2.0/` (daily, pentad, monthly GeoTIFF / NetCDF) |
| Auth | None — file download |
| License | Public domain (credit UCSB Climate Hazards Center) |
| Used by | 06 (drought: SPI-1 / SPI-3 history since 1981) |

### 1.10 Vegetation and terrain 📄

| Data | Source | Access | Used by |
|---|---|---|---|
| NDVI (MODIS MOD13Q1, 16-day 250 m) | NASA LP DAAC / AppEEARS (or Google Earth Engine) | Free Earthdata Login → **user token** in `EARTHDATA_TOKEN` (urs.earthdata.nasa.gov → Generate Token; valid 60 days, max 2 active; Python `earthaccess` reads it) 🔑 | 06 drought, fire |
| NDVI (Sentinel-2) | Copernicus Data Space | Free account 🔑 | 06 (optional, higher resolution) |
| DEM (Copernicus GLO-30 / SRTM) | Copernicus / OpenTopography | Free download | 05 flood features |

### 1.11 Thai admin boundaries ✅

| Item | Detail |
|---|---|
| Source | OCHA HDX `cod-ab-tha` — <https://data.humdata.org/dataset/cod-ab-tha> |
| API | `https://data.humdata.org/api/3/action/package_show?id=cod-ab-tha` ✅ |
| Data | Admin levels 0–3 (country, province, amphoe, tambon) with P-codes, Thai and English names |
| License | Check the dataset page ⚠️ (HDX COD-AB is usually CC BY-IGO) |
| Used by | 05 (area table), 01 (map), 03 (gazetteer) |

### 1.12 Air4Thai — PM2.5 stations (กรมควบคุมมลพิษ) ✅

| Item | Detail |
|---|---|
| Endpoint | `https://air4thai.pcd.go.th/services/getNewAQI_JSON.php` (all stations, latest hour) |
| Auth | None |
| Data | ~173 stations (`GROUND`, `BKK`, `MOBILE`); `AQILast.PM25.value` = 24-hour average PM2.5 (µg/m³), `-1` = no reading; date and time in Thai local time |
| Quirks | Some stations lag many hours (27 of 170 were over 6 h old on 2026-10-22 03:00), so readings older than 6 h are dropped. The server sends the wrong intermediate certificates; Python needs the Let's Encrypt `YR1` + `Root YR` intermediates (bundled in `adapters/air4thai.py`, valid to 2028-09-02). |
| Thresholds | PCD PM2.5 bands since 2023-06-01: 0–15 very good, 15.1–25 good, 25.1–37.5 moderate, 37.6–75 starts to affect health, ≥ 75.1 affects health |
| Used by | 04, 02 (`air_quality` hazard) |

### 1.13 Open-Meteo Air Quality — CAMS PM2.5 forecast ✅

| Item | Detail |
|---|---|
| Endpoint | `https://air-quality-api.open-meteo.com/v1/air-quality?latitude=..&longitude=..&hourly=pm2_5` (comma-separated coordinates return a list) |
| Auth and limits | None; same Open-Meteo limits as 1.6, and **each coordinate counts as a call**. 928 district points in one go got 429 on 2026-10-22, so points snap to the 0.4° CAMS grid (306 calls) and refresh every 6 h |
| Use | Only for districts without an Air4Thai station; the model alone gives at most WATCH |
| License | CC BY 4.0 — credit "Air quality data by Open-Meteo.com / CAMS" |

---

## 2. Map and location APIs

| API | What | Auth | Free tier | Status | Used by |
|---|---|---|---|---|---|
| **GISTDA Sphere Map API** | Thai basemap, JS/React/Flutter SDK, search, routing, elevation. Script: `https://api.sphere.gistda.or.th/map/?key=<KEY>`, docs <https://api.sphere.gistda.or.th/map/doc.html> | Key from <https://portal.sphere.gistda.or.th/account/signup/> | Free for personal / non-commercial use | 🔑 (returns "sphere API Key Error" without a key) | 01 |
| **Longdo Map API** | Thai basemap, Thai geocoding and search, REST services | Key from <https://api.longdo.com/console> | Reported ~100,000 service calls/month, 60/min, 5,000/day ⚠️ | 🔑 (returns "API Key Error" without a key) | 01, 03 |
| **OpenStreetMap + MapLibre** | Basemap through a free tile provider; no vendor lock-in | None (tile provider may need a key) | Follow the tile provider's policy | 📄 | 01 |
| **Nominatim (OSM)** | Geocoding — Thai names work (tested "อำเภอบางบัวทอง") | None; must send a real User-Agent | Max **1 request/second**, no bulk geocoding | ✅ | 03 (fallback), 05 |

**Recommendation:** use MapLibre with an OSM basemap for development (no key). Switch to GISTDA Sphere for the demo, because it is a Thai government map and matches the project. For geocoding, the main tool is our own gazetteer built from the HDX boundaries (offline, no limit); use Nominatim or Longdo only as a fallback.

---

## 3. AI APIs

| API | Use | Auth | Cost | Used by |
|---|---|---|---|---|
| **LLM — Gemini** `gemini-3.5-flash-lite` (in use) via `google-genai` SDK, `client.models.generate_content` | Rewrites rule-based answers in plain Thai (03 → 07 explainer) | `GEMINI_API_KEY` from https://aistudio.google.com/apikey | Free tier: yes (requests may be used to improve Google products). Paid: $0.75 / $3.75 per 1M tokens in/out until 2026-12-31, then $1.50 / $7.50 ⚠️ | 03, 07 |
| Stronger option: `gemini-3.8-flash` | If Thai wording needs to be better | same key | Free tier: yes. Paid $0.75 / $3.75 until 2026-12-31, then $1.50 / $7.50 | 03, 07 |
| Not chosen: `gemini-4-argon` | Flagship; no free tier, $2 / $10 rising to $4 / $20 on 2026-12-01 — more than this task needs | same key | Paid only | — |
| **Embedding** — `BAAI/bge-m3` or `intfloat/multilingual-e5-large` via sentence-transformers | RAG vectors for Thai and English | None (runs locally) | Free | 07 |
| **Reranker** — `BAAI/bge-reranker-v2-m3` | RAG reranking | None (local) | Free | 07 |

The system must still work when the LLM is unavailable: with no key, a failed call, or an answer that fails validation (changed alert level, invented number, dropped emergency numbers or no-forecast notice), the rule-based answer is shown. Checked 2026-10-19 against https://ai.google.dev/gemini-api/docs/models and /pricing; `gemini-2.0-flash` and `gemini-3-pro-preview` are already shut down.

---

## 4. Notification and identity APIs

| API | Use | Auth | Free tier | Status | Used by |
|---|---|---|---|---|---|
| **LINE Messaging API** | Push alerts; receive questions through a webhook | Channel access token + channel secret (LINE Developers console) | Thailand free plan: **300 push messages/month**. Replies are free. Basic ฿1,280 = 15,000 msgs ⚠️ | 📄 | 08 (alerts), 02/03 (questions) |
| **LINE Login** | Sign in with LINE | Channel ID + secret | Free | 📄 | 01, 02 |
| **Web Push (VAPID)** | Browser push alerts | Self-generated VAPID key pair | Free | 📄 | 01, 08 |
| **SMTP email** | Backup alerts | SMTP account | Provider-dependent | 📄 | 08 |
| **Keycloak** | OIDC identity provider | Runs in our docker-compose | Free (self-hosted) | 📄 | 02 |

The free LINE plan (300 pushes/month) is enough only for a small demo group. Use replies (free) for Q&A, and group several hazards into one push.

---

## 5. Environment variables

The block below is a reference. The real template is `.env.example`, which keeps every comment on its own line: Docker Compose reads `FIRMS_MAP_KEY=   # optional` as the value `# optional` (found in testing; it sent FIRMS a URL containing the comment).

```dotenv
# --- Hazard data ---
TMD_UID=api                     # TMD public demo uid; replace with your own
TMD_UKEY=api12345               # TMD public demo ukey; replace with your own
TMD_NWP_TOKEN=                  # 🔑 required for NWP forecast
THAIWATER_BASE_URL=https://api-v3.thaiwater.net/api/v1/thaiwater30/public
FIRMS_MAP_KEY=                  # 🔑 optional for MVP (keyless 24h file works)
GISTDA_API_KEY=                 # 🔑 flood extent, hotspots
EARTHDATA_TOKEN=                # 🔑 NASA Earthdata user token (NDVI); expires after 60 days
OPEN_METEO_BASE_URL=https://api.open-meteo.com/v1

# --- Map ---
NEXT_PUBLIC_SPHERE_KEY=         # 🔑 GISTDA Sphere (optional)
LONGDO_API_KEY=                 # 🔑 optional
NOMINATIM_USER_AGENT=ThanPhai/0.1 (contact: team email)

# --- AI ---
LLM_PROVIDER=gemini
LLM_MODEL=gemini-3.5-flash-lite
LLM_THINKING_LEVEL=LOW
GEMINI_API_KEY=                 # 🔑 Google AI Studio
EMBEDDING_MODEL=BAAI/bge-m3

# --- Notifications ---
LINE_CHANNEL_ACCESS_TOKEN=      # 🔑
LINE_CHANNEL_SECRET=            # 🔑
VAPID_PUBLIC_KEY=
VAPID_PRIVATE_KEY=
```

---

## 6. Attribution text (show in the web app footer and the about page)

- ข้อมูลอุตุนิยมวิทยา: กรมอุตุนิยมวิทยา (TMD)
- ข้อมูลน้ำ: คลังข้อมูลน้ำแห่งชาติ (สสน.) และหน่วยงานเครือข่าย
- ข้อมูลภาพถ่ายดาวเทียมและน้ำท่วม: GISTDA
- Fire data: NASA FIRMS
- Weather data by Open-Meteo.com (CC BY 4.0)
- ข้อมูลคุณภาพอากาศ: กรมควบคุมมลพิษ (Air4Thai)
- Air quality data by Open-Meteo.com / CAMS (CC BY 4.0)
- Rainfall: CHIRPS, UCSB Climate Hazards Center
- Boundaries: OCHA HDX
- Map data © OpenStreetMap contributors (if the OSM basemap is used)

---

## 7. Limits found while running the system (2026-10-19)

| Provider | What the API actually enforces | How ThanPhai handles it |
|---|---|---|
| TMD NWP | `x-ratelimit-limit: 60` per minute, `retry-after` on 429 (the docs give no number) | 77 province calls in two windows; waits for `retry-after` |
| Gemini free tier | A few requests per minute; 24 calls in ~1 minute all got 429 | Answer cache, 60 s pause after a 429, max 4 calls at once; template answer otherwise |
| GISTDA | No rate-limit headers; ~80,000 flood cells per 7 days | Counts per district with `limit=1` + `numberMatched` (~400 calls, 17 s) instead of downloading polygons |
| FIRMS | 5,000 calls / 10 min; area API max 5 days per call | History in 5-day chunks; live model uses 6 calls for 30 days |
| Open-Meteo | Calls weighted by data volume | History cached; CHIRPS planned for bulk history |

Security: GISTDA echoes the API key inside response links, and FIRMS puts its key in the URL path (httpx logged
it at INFO). `thanphai_shared.redact` removes every configured key from logs and error messages.
