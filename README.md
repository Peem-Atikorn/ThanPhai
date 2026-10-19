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
| 01 | `01_web_app` | Next.js, map layers, area subscription | | |
| 02 | `02_api_backend` | FastAPI gateway, auth, cache | | |
| 03 | `03_hazard_ai_agent` | LLM planner, tool routing | | |
| 04 | `04_external_data_services` | Data source adapters | | |
| 05 | `05_geo_data_integration` | PostGIS, canonical schema, features | | |
| 06 | `06_hazard_risk_models` | Four hazard risk models | | |
| 07 | `07_knowledge_decision_llm` | RAG, alert rules, explanation | | |
| 08 | `08_alert_feedback_monitoring` | Alerts, feedback, monitoring | | |

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
| `feature/<module>-<topic>` | One task per branch, e.g. `feature/08-line-alerts`, `fix/04-tmd-empty-stations`. |

**Branch names** (checked automatically on every push and pull request): `<type>/<module>-<topic>`

| Part | Allowed |
|---|---|
| type | `feature` new work · `fix` bug fix · `hotfix` urgent fix for `main` · `docs` · `refactor` · `test` · `chore` (tooling, deps) |
| module | `01`–`08` for one module, `00` for shared code or infrastructure, `all` for the whole project |
| topic | lowercase English words joined by `-` |

Examples: `feature/08-line-alerts`, `fix/04-tmd-empty-stations`, `docs/all-setup-guide`, `chore/00-docker-compose`.
Pull requests into `main` may come only from `develop` or `hotfix/*`; `main` and `develop` accept changes only through pull requests.

```bash
git clone https://github.com/Peem-Atikorn/ThanPhai.git
cd ThanPhai
git switch develop
git switch -c feature/08-line-alerts
# ... work, then run: make test
git push -u origin feature/08-line-alerts   # open a Pull Request into develop
```

* Copy `.env.example` to `.env` and fill in your own keys. **Never commit `.env`.**
* `data/raw` and `data/processed` are not in Git (≈1.4 GB). Build them with `make data` (and `make ndvi`, `make fire-model` if you need drought/fire).
* Run `make test` before opening a Pull Request. `PROGRESS.md` lists what is done and what is next.
