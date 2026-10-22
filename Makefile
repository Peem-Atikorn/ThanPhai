PY ?= .venv/Scripts/python
MODULES = 02_api_backend 03_hazard_ai_agent 04_external_data_services 05_geo_data_integration 06_hazard_risk_models 07_knowledge_decision_llm 08_alert_feedback_monitoring

.PHONY: setup data test test-live snapshot ndvi fire-model landslide api web up down logs

setup:
	python -m venv .venv
	$(PY) -m pip install -e shared -e "04_external_data_services[dev]" -e "05_geo_data_integration[dev]" \
		-e "06_hazard_risk_models[dev]" -e "07_knowledge_decision_llm[dev]" -e "03_hazard_ai_agent[dev]" \
		-e "02_api_backend[dev]" -e "08_alert_feedback_monitoring[dev]" sqlalchemy geoalchemy2 "psycopg[binary]"
	cd 01_web_app/web && npm ci

# Needs data/raw/hdx/gdb/tha_admin_boundaries.gdb (HDX cod-ab-tha)
data:
	$(PY) scripts/build_admin_areas.py

test:
	@for m in $(MODULES); do echo "== $$m"; (cd $$m && ../$(PY) -m pytest -q) || exit 1; done

test-live:
	cd 04_external_data_services && ../$(PY) -m pytest -q -m live

snapshot:
	$(PY) scripts/live_area_snapshot.py

# Monthly: MODIS NDVI -> drought VCI (needs EARTHDATA_TOKEN and pip install -e "06_hazard_risk_models[ndvi]")
ndvi:
	$(PY) scripts/build_ndvi_vci.py

# Rarely (DMR updates the inventory every ~5 years): past landslides per district
landslide:
	$(PY) scripts/build_landslide_districts.py

# Yearly: retrain the fire model on FIRMS history (needs FIRMS_MAP_KEY)
fire-model:
	$(PY) scripts/download_firms_history.py
	$(PY) scripts/train_fire_model.py

api:
	$(PY) -m uvicorn api_backend.main:app --port 8002

web:
	cd 01_web_app/web && npm run dev

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f
