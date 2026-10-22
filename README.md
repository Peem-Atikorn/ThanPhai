# ทันภัย — ThanPhai

AI multi-hazard early warning assistant for Thailand

ThanPhai combines official weather, water, satellite, and disaster data into one system and estimates short-term risk for four hazards at district (amphoe) and subdistrict (tambon) level:

| Hazard | Thai | Horizon | Main output |
|---|---|---|---|
| Heavy rain / storm | ฝนหนัก / พายุ | 0–72 hours | Risk level per area and time window |
| Flood | น้ำท่วม | 0–72 hours | Flood risk level per area with water-level trend |
| Drought | ภัยแล้ง | 1–3 months | Drought index and trend per area |
| Wildfire / grass fire | ไฟป่า / ไฟหญ้า | 0–7 days | Fire probability per area |

The system combines an AI agent, external data adapters, geospatial data integration, four hazard risk models, disaster-focused RAG, deterministic alert rules, an LLM-generated explanation, and an alert and feedback loop inside a Docker-based architecture.

> **Scope:** ThanPhai is a **risk nowcasting** system, not a numerical weather model. It uses official forecasts and observations as inputs and does not replace warnings from TMD, DDPM, or other authorities. Official warnings always override model output.

----

# Structure

```text
ThanPhai/
│
├── 01_web_app/                              # Map dashboard, area subscription, and alerts UI
│   ├── 01_env.txt                           # Frontend runtime, packages, and configuration
│   ├── 02_step.txt                          # User input and risk display workflow
│   └── 03_process.txt                       # UI architecture, map layers, accessibility, and UX
│
├── 02_api_backend/                          # Secure API gateway and request management
│   ├── 01_env.txt
│   ├── 02_step.txt
│   └── 03_process.txt
│
├── 03_hazard_ai_agent/                      # Agent orchestration, area resolution, tool selection
│   ├── 01_env.txt
│   ├── 02_step.txt
│   └── 03_process.txt
│
├── 04_external_data_services/               # Adapters for TMD, ThaiWater, GISTDA, FIRMS, DDPM
│   ├── 01_env.txt
│   ├── 02_step.txt
│   └── 03_process.txt
│
├── 05_geo_data_integration/                 # Normalization, geospatial join, feature store
│   ├── 01_env.txt
│   ├── 02_step.txt
│   └── 03_process.txt
│
├── 06_hazard_risk_models/                   # Four risk models: rain/storm, flood, drought, fire
│   ├── 01_env.txt
│   ├── 02_step.txt
│   └── 03_process.txt
│
├── 07_knowledge_decision_llm/               # Disaster RAG, alert-level rules, LLM explanation
│   ├── 01_env.txt
│   ├── 02_step.txt
│   └── 03_process.txt
│
├── 08_alert_feedback_monitoring/            # Notifications, ground reports, model monitoring
│   ├── 01_env.txt
│   ├── 02_step.txt
│   └── 03_process.txt
│
└── docs/
    └── DATA_SOURCES.md                      # Candidate data sources per hazard
```

# Data Flow

```text
User (web / LINE)
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

7 people, 8 modules. Module 06 is the largest, so it may be split between two people (for example rain/storm + flood, and drought + fire).

| # | Module | Main work | Owner | Branch |
|---|---|---|---|---|
| 01 | `01_web_app` | Next.js, map layers, area subscription | mahawongsupawit125-coder | |
| 02 | `02_api_backend` | FastAPI gateway, auth, cache | | |
| 03 | `03_hazard_ai_agent` | LLM planner, tool routing | | |
| 04 | `04_external_data_services` | Data source adapters | | |
| 05 | `05_geo_data_integration` | PostGIS, canonical schema, features | | |
| 06 | `06_hazard_risk_models` | Four hazard risk models | cheewakorn | |
| 07 | `07_knowledge_decision_llm` | RAG, alert rules, explanation | cheewakorn | |
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
make snapshot    # live data -> 928 districts x 3 hazards
```

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
docker compose up -d
```

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
* `data/raw` and `data/processed` are not in Git (≈1.4 GB). Build them with `make data` (and `make ndvi`, `make fire-model` if you need drought/fire).
* Run `make test` before opening a Pull Request. `PROGRESS.md` lists what is done and what is next.

### Open tasks (pick one, put your name in the branch)

| Module | Task | Branch name example | Needs |
|---|---|---|---|
| 08 Alerts | Send alerts through LINE when an area a user follows reaches WARNING/CRITICAL; store subscriptions | `feature/<name>/08-line-alerts` | LINE Messaging API channel |
| 08 Alerts | Ground reports: users report flooding/smoke with a photo and location | `feature/<name>/08-ground-reports` | — |
| 01 Web | "Follow this area" button and a page listing followed areas (pairs with 08) | `feature/<name>/01-follow-area` | — |
| 05 Geo | Read TMD warning text and map it to the provinces it names, so warnings apply per district | `feature/<name>/05-warning-provinces` | — |
| 04 Data | Water quality (42 ThaiWater stations: DO, pH, salinity) as a layer | `feature/<name>/04-water-quality` | — |
| 04 Data | Active tropical storms from ThaiWater `storm_data` (name, category, track) | `feature/<name>/04-active-storms` | — |
| 06 Models | CHIRPS rainfall → SPI drought index per district (drought can then reach WARNING) | `feature/<name>/06-chirps-spi` | ~2 GB download |
| 06 Models | Flood model trained on GISTDA flood history | `feature/<name>/06-flood-model` | GISTDA key |
| 07 Knowledge | RAG over DDPM disaster guides so the assistant can answer "what should I do" | `feature/<name>/07-ddpm-rag` | DDPM guide PDFs |
| 02 API | Login (Keycloak) for subscriptions and reports | `feature/<name>/02-keycloak-auth` | — |

Details and what is already done: `PROGRESS.md` ("Next steps").
