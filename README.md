# ทันภัย — ThanPhai

AI multi-hazard early warning assistant for Thailand

ThanPhai combines official weather, water, satellite, and disaster data into one system and gives a risk level for
ten hazards in each of Thailand's 928 districts (amphoe), with live data only:

| Hazard | Thai | Main inputs |
|---|---|---|
| Heavy rain / storm | ฝนหนัก / พายุ | TMD forecasts and warnings, Open-Meteo, ThaiWater rain gauges |
| Flood | น้ำท่วม | ThaiWater water levels (readings older than 6 h dropped), GISTDA flood satellite, rain forecast |
| Drought | ภัยแล้ง | SPI from ERA5 rainfall history, MODIS NDVI/VCI |
| Wildfire / grass fire | ไฟป่า / ไฟหญ้า | NASA FIRMS hotspots, fire model (NDVI, humidity) |
| PM2.5 | ฝุ่น PM2.5 | Air4Thai stations |
| Heat | ความร้อนจัด | Feels-like temperature (Open-Meteo) |
| Earthquake | แผ่นดินไหว | TMD and USGS earthquakes (expected shaking per district); a month of TMD events kept |
| Marine wind and waves | คลื่นลมแรง | Open-Meteo Marine wave height, TMD warnings |
| Tropical cyclone | พายุหมุนเขตร้อน | ThaiWater storm list, GDACS cyclone tracks |
| Landslide | ดินถล่ม | Rain in districts with past landslides (DMR inventory) |

Beyond the hazard levels, the web app shows large dams (storage, inflow, release, history), hourly river flow and bank
levels with a river-level forecast, temporary disaster shelters (DDPM) with nearest-shelter search and safe route planning
avoiding satellite flood polygons (openrouteservice), water-quality stations, ThaiWater radar and forecast images, and RainViewer radar
with past-hour playback and a 1-hour nowcast. A Thai/English assistant answers questions from the same data.

How the levels and answers are made:

* **Alert levels come only from deterministic rules** (`07_knowledge_decision_llm/decision_engine/rules.yaml`).
* **The LLM only rewrites wording**: Qwen (`thanphai-qwen` on Ollama) first, Gemini as a fallback (`LLM_PROVIDER`).
  Every rewrite passes `validate()`: a number or level not in the facts means the template answer is shown instead.
  Official TMD bulletin text, preparation guidance and emergency numbers are never rewritten.
* **Official warnings always override model output.**

> **Scope:** ThanPhai is a **risk nowcasting** system, not a numerical weather model. It uses official forecasts and observations as inputs and does not replace warnings from TMD, DDPM, or other authorities.

----

# Structure

Each module folder keeps `01_env.txt`, `02_step.txt` and `03_process.txt` (design notes) next to its code.

```text
ThanPhai/
├── 01_web_app/web/                 # Next.js + MapLibre: dashboard, map, radar playback, dams, rivers, assistant chat
├── 02_api_backend/api_backend/     # FastAPI: fetch, cache (stale-while-revalidate), ask rate limit; runs 03/05/07 in-process
├── 03_hazard_ai_agent/hazard_agent/  # Question understanding (Thai/English), data tools, answers
├── 04_external_data_services/external_data/  # Adapters: TMD, ThaiWater, GISTDA, FIRMS, Air4Thai, Open-Meteo, GDACS, RainViewer, OpenRouteService
├── 05_geo_data_integration/geo_integration/  # Gazetteer, point -> district mapping, features, regions
├── 06_hazard_risk_models/risk_models/        # Drought (SPI, NDVI/VCI), fire, water level and dam inflow, radar nowcast
├── 07_knowledge_decision_llm/
│   ├── decision_engine/            # rules.yaml, decision engine, DDPM guide RAG, LLM explainer + validate()
│   └── training/                   # Qwen explainer dataset generator and LoRA training
├── 08_alert_feedback_monitoring/alerting/    # Alert policy (delivery over LINE is not built yet)
├── shared/thanphai_shared/         # Shared types (hazards, alert levels)
├── scripts/                        # Data builds, model training, live snapshots, start-docker.ps1
├── docs/                           # API_REQUIREMENTS.md (sources and quirks), DATA_SOURCES.md, MAP_OVERLAYS.md
└── data/                           # raw/ and processed/, not in Git
```

# Data Flow

```text
User (web; LINE planned)
   │
   ▼
01 Web App ──► 02 API Backend ──► 03 Hazard AI Agent
                                     │
              ┌──────────────────────┼─────────────────────────┐
              ▼                      ▼                         ▼
   04 External Data ──► 05 Geo Data Integration ──► 06 Hazard Risk Models
                                                               │
                                                               ▼
                                         07 Knowledge, Decision & LLM
                                                               │
                                                               ▼
                                         08 Alert, Feedback & Monitoring
```

# Alert Levels

Every area and hazard gets one alert level. The level is decided by deterministic rules in module 07, never by the LLM.

| Level | Thai | Color | Meaning |
|---|---|---|---|
| `NORMAL` | ปกติ | Green | No significant risk in the forecast window |
| `WATCH` | เฝ้าระวัง | Yellow | Risk is rising; follow updates |
| `WARNING` | เตือนภัย | Orange | Hazard is likely; prepare and follow official guidance |
| `CRITICAL` | วิกฤต | Red | Hazard is occurring or imminent, or an official emergency is active |

Every assessment must include its hazard, area, time window, alert level, confidence, reasons, source citations, data freshness, model version, and any unavailable or degraded data sources.

# Team Assignment

3 people, 8 modules. Each module has one owner; changes that span several modules use `all` in the branch name
(for example `feature/peem/all-public-site`). Branches are the ones merged into `develop` through pull requests.

| # | Module | Main work | Owner | Branch |
|---|---|---|---|---|
| 01 | `01_web_app` | Next.js, map layers, radar playback, assistant chat | mahawongsupawit125-coder | `feature/supawit/01-web-app`<br>`feature/supawit/01-overview-pm25-heat`<br>`feature/supawit/01-storms-area-warnings`<br>`feature/supawit/01-radar-playback`<br>`feature/supawit/01-radar-nowcast`<br>`fix/supawit/01-compact-map-header`<br>`feature/supawit/all-dashboard-update` |
| 02 | `02_api_backend` | FastAPI gateway, cache, rate limit | Peem-Atikorn | `feature/peem/02-map-overlays`<br>`feature/peem/02-radar-access`<br>`refactor/peem/02-radar-adapter`<br>`feature/peem/all-shelters-route` |
| 03 | `03_hazard_ai_agent` | Question understanding, tool routing, answers | mahawongsupawit125-coder | `feature/supawit/03-answer-templates`<br>`feature/supawit/03-overview-ranking`<br>`feature/supawit/03-agent-data-tools`<br>`fix/supawit/03-ambiguous-district`<br>`fix/supawit/03-warning-scope` |
| 04 | `04_external_data_services` | Data source adapters | Peem-Atikorn | `feature/peem/04-water-quality-storms`<br>`feature/titleww/04-radar-nowcast` |
| 05 | `05_geo_data_integration` | Gazetteer, area mapping, features | Peem-Atikorn | `feature/peem/05-tmd-warning-provinces`<br>`feature/peem/05-tmd-warning-region-days`<br>`feature/supawit/05-area-names` |
| 06 | `06_hazard_risk_models` | Fire, drought and water-level models | cheewakorn (titleww) | `feature/cheewakorn/06-fire-ndvi-humidity-v2`<br>`feature/peem/06-water-level-forecast` |
| 07 | `07_knowledge_decision_llm` | RAG, alert rules, Qwen explainer and warning audit | cheewakorn (titleww) | `feature/cheewakorn/07-ddpm-rag`<br>`feature/titleww/07-qwen-model`<br>`feature/titleww/07-qwen-warning-audit` |
| 08 | `08_alert_feedback_monitoring` | Alerts, feedback, monitoring | mahawongsupawit125-coder | |

----

## How to Run

Current status and test results: see [`PROGRESS.md`](PROGRESS.md). All eight modules have runnable code; what still needs API keys is listed there.

### 1. Boundary data (once)

Download `tha_admin_boundaries.gdb.zip` from [HDX cod-ab-tha](https://data.humdata.org/dataset/cod-ab-tha) (CC BY-IGO), unzip to `data/raw/hdx/gdb/`, then:

```bash
make data
```

### 2. Local development (Python 3.12+, Node 20+)

```bash
make setup
```

```bash
make test        # offline tests for all Python modules
make test-live   # calls the real provider APIs
make snapshot    # live data -> every district, end to end
```

Models and extra data (only when needed): `make shelters` (DDPM disaster shelters), `make ndvi` (monthly drought NDVI), `make fire-model` (yearly),
`make water-model` (water level and dam inflow), `make landslide` (rarely).

```bash
make api         # API on http://localhost:8002/docs
make web         # web map on http://localhost:3000
```

### 3. Docker

On this Windows machine, start Docker with the script below. Every Windows shutdown or sleep leaves stale socket files
that make Docker Desktop crash on start; the script moves them aside, starts Docker, and runs `docker compose up -d`.

```powershell
.\scripts\start-docker.ps1
```

**Docker Compose** is the main orchestrator for the complete system:

* **`docker-compose.yml`** — defines and connects all services
* **`Dockerfile`** — environment and dependencies for each service
* **`.env`** — API keys and private configuration (never commit it; keep `.env.example`)
* **Docker Network** — communication between containers
* **`Makefile`** *(optional)* — shortcuts such as `make up`, `make down`, `make logs`

```bash
docker compose up -d --build api-backend web
```

Web: http://localhost:3000 · API: http://127.0.0.1:8002/docs. Both bind to localhost only.

* **Public site:** `docker compose --profile public up -d` starts a Cloudflare Tunnel to the web container
  (needs `CLOUDFLARE_TUNNEL_TOKEN` in `.env`; no inbound port is opened). `ASK_LIMIT_PER_MINUTE` limits questions per visitor.
* **Shelter routes:** set `ORS_API_KEY` in `.env` to enable driving/walking directions around flood zones; `ROUTE_LIMIT_PER_MINUTE` limits route requests per visitor.
* **LLM:** run Ollama with the `thanphai-qwen` model on the host (`QWEN_ENDPOINT`), and set `GEMINI_API_KEY` for the fallback.
  With neither, every answer is the rule-based template.
* After a restart of `api-backend`, satellite and forecast inputs take about a minute to load; the page waits for them.

---

## Team workflow (Git)

| Branch | Use |
|---|---|
| `main` | Working version for demos and submission. Merge into it from `develop` only. |
| `develop` | Shared working branch. Every feature branch starts from here and merges back here. |
| `feature/<name>/<module>-<topic>` | One task per branch, e.g. `feature/supawit/08-line-alerts`, `fix/peem/04-tmd-empty-stations`. |

**Branch names** (checked automatically on every push and pull request): `<type>/<name>/<module>-<topic>`

| Part | Allowed |
|---|---|
| type | `feature` new work · `fix` bug fix · `hotfix` urgent fix for `main` · `docs` · `refactor` · `test` · `chore` (tooling, deps) |
| name | who owns the branch: your lowercase nickname, 2–20 letters/digits, the same every time (e.g. `peem`, `supawit`) |
| module | `01`–`08` for one module, `00` for shared code or infrastructure, `all` for the whole project |
| topic | lowercase English words joined by `-` |

Examples: `feature/supawit/08-line-alerts`, `fix/peem/04-tmd-empty-stations`, `docs/peem/all-setup-guide`, `chore/supawit/00-docker-compose`.
Pull requests into `main` may come only from `develop` or `hotfix/*`; `main` and `develop` accept changes only through pull requests.

**Notifications:** `.github/CODEOWNERS` asks every team member to review each pull request. To also get comments and pushes,
open the repository on GitHub, click **Watch → All Activity**, and turn on push notifications in the GitHub mobile app.

```bash
git clone https://github.com/Peem-Atikorn/ThanPhai.git
cd ThanPhai
git switch develop
git switch -c feature/supawit/08-line-alerts
# ... work, then run: make test
git push -u origin feature/supawit/08-line-alerts   # open a Pull Request into develop
```

* Copy `.env.example` to `.env` and fill in your own keys. **Never commit `.env`.**
* `data/raw` and `data/processed` are not in Git (about 2 GB together). Build them with `make data` (and `make ndvi`, `make fire-model`, `make water-model` when you need those models).
* Run `make test` before opening a Pull Request. `PROGRESS.md` lists what is done and what is next.

### Open tasks (pick one, put your name in the branch)

| Module | Task | Branch name example | Needs |
|---|---|---|---|
| 08 Alerts | Send alerts through LINE when an area a user follows reaches WARNING/CRITICAL; store subscriptions | `feature/<name>/08-line-alerts` | LINE Messaging API channel |
| 08 Alerts | Ground reports: users report flooding/smoke with a photo and location | `feature/<name>/08-ground-reports` | — |
| 01 Web | "Follow this area" button and a page listing followed areas (pairs with 08) | `feature/<name>/01-follow-area` | — |
| 06 Models | CHIRPS rainfall backfill → SPI per district from local history (drought can then reach WARNING/CRITICAL) | `feature/<name>/06-chirps-spi` | ~2 GB download |
| 06 Models | Flood model trained on GISTDA flood history | `feature/<name>/06-flood-model` | GISTDA key |
| 02 API | Login (Keycloak) for subscriptions and reports | `feature/<name>/02-keycloak-auth` | — |

Details and what is already done: `PROGRESS.md` ("Next steps").
