# Build from the repo root:  docker build -f 02_api_backend/Dockerfile .
# Needs data/processed (gazetteer.json, admin_areas.gpkg, admin_areas_web.gpkg) mounted at /data/processed.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 DATA_DIR=/data/processed
WORKDIR /app

COPY shared /src/shared
COPY 04_external_data_services /src/04
COPY 05_geo_data_integration /src/05
COPY 06_hazard_risk_models /src/06
COPY 07_knowledge_decision_llm /src/07
COPY 03_hazard_ai_agent /src/03
COPY 02_api_backend /src/02
RUN pip install --no-cache-dir /src/shared /src/04 /src/05 /src/06 /src/07 /src/03 /src/02

RUN useradd --create-home app
USER app

EXPOSE 8002
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8002/health')"
CMD ["uvicorn", "api_backend.main:app", "--host", "0.0.0.0", "--port", "8002"]
