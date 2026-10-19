# Build from the repo root:  docker build -f 04_external_data_services/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
WORKDIR /app

COPY shared /src/shared
COPY 04_external_data_services /src/service
RUN pip install --no-cache-dir /src/shared /src/service

RUN useradd --create-home app
USER app

EXPOSE 8004
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8004/health')"
CMD ["uvicorn", "external_data.main:app", "--host", "0.0.0.0", "--port", "8004"]
