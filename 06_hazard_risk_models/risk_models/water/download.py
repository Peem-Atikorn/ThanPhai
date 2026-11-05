"""Download ThaiWater history for the water-level and dam-inflow models.

- River stations: hourly-ish level (m MSL) from `public/waterlevel_graph`, in 90-day chunks (a year in one request
  sometimes times out). Each chunk is saved as gzip JSON, so a stopped run resumes where it left off.
- Dams: one value per day for every year ThaiWater has, from `analyst/dam_yearly_graph` (inflow, release, storage).

One request at a time with a short pause: ThaiWater is a public service, and the history is fetched once.
"""

import gzip
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

from external_data.adapters.thaiwater import ThaiWaterWaterLevel
from external_data.adapters.thaiwater_graphs import ROOT as THAIWATER_ROOT

log = logging.getLogger("water.download")
USER_AGENT = {"User-Agent": "ThanPhai/0.1 (student project; history for a water-level model)"}
CHUNK_DAYS = 90
PAUSE_S = 0.4
DAM_SERIES = ("dam_inflow", "dam_released", "dam_storage")


def _write_gz(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    tmp.replace(path)


def river_stations(client: httpx.Client) -> list[dict]:
    """River stations worth a model: a trusted bank level, and a flow gauge or RID key-station status.

    Grouped like the app's river view (one physical station is often listed as "C.13" and "ridhydro_C.13"); the
    key-station row is the one whose id carries the graph history. Only stations reporting in the last two days.
    """
    adapter = ThaiWaterWaterLevel()
    response = client.get(f"{THAIWATER_ROOT}/public/{adapter.endpoint}", timeout=adapter.timeout_seconds)
    response.raise_for_status()
    records = adapter.parse(response.json(), datetime.now(timezone.utc))
    cutoff = datetime.now(timezone.utc) - timedelta(days=2)
    groups: dict[tuple, list] = {}
    for r in records:
        code = r.extra.get("station_code")
        if code and r.station_id and r.lat is not None and r.observed_at and r.observed_at >= cutoff:
            groups.setdefault((code, r.province_code), []).append(r)
    stations = []
    for (code, province), group in groups.items():
        bank = next((r for r in group if r.extra.get("bank_quality") == "ok" and r.extra.get("min_bank") is not None), None)
        if bank is None or not any(r.extra.get("discharge_m3s") or r.extra.get("is_key_station") for r in group):
            continue
        main = sorted(group, key=lambda r: not r.extra.get("is_key_station"))[0]
        first = lambda key, group=group: next((r.extra.get(key) for r in group if r.extra.get(key) is not None), None)  # noqa: E731
        stations.append({"id": main.station_id, "code": code, "name_th": first("name_th"), "agency": first("agency_en"),
                         "basin_th": first("basin_th"), "river_th": first("river_name"), "province_code": province,
                         "lat": main.lat, "lon": main.lon, "bank_msl": bank.extra["min_bank"], "qmax_m3s": first("qmax_m3s")})
    return stations


def download_rivers(out: Path, stations: list[dict], start: date, end: date, client: httpx.Client) -> dict:
    """Fetch every station's chunks between start and end; returns counts of fetched, skipped and failed chunks."""
    counts = {"fetched": 0, "skipped": 0, "failed": 0}
    chunks = []
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + timedelta(days=CHUNK_DAYS), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end
    for n, station in enumerate(stations, 1):
        for chunk_start, chunk_end in chunks:
            path = out / "rivers" / station["id"] / f"{chunk_start.isoformat()}.json.gz"
            if path.exists():
                counts["skipped"] += 1
                continue
            params = {"station_type": "tele_waterlevel", "station_id": station["id"],
                      "start_date": chunk_start.isoformat(), "end_date": chunk_end.isoformat()}
            for attempt in range(3):
                try:
                    response = client.get(f"{THAIWATER_ROOT}/public/waterlevel_graph", params=params, timeout=90)
                    response.raise_for_status()
                    data = (response.json() or {}).get("data") or {}
                    rows = [[row.get("datetime"), row.get("value"), row.get("discharge")] for row in data.get("graph_data") or []]
                    _write_gz(path, {"station_id": station["id"], "start": params["start_date"], "end": params["end_date"],
                                     "min_bank": data.get("min_bank"), "ground_level": data.get("ground_level"),
                                     "qmax": data.get("qmax"), "rows": rows})
                    counts["fetched"] += 1
                    break
                except (httpx.HTTPError, ValueError) as exc:
                    log.warning("station %s %s attempt %d: %s", station["id"], chunk_start, attempt + 1, exc)
                    time.sleep(5 * (attempt + 1))
            else:
                counts["failed"] += 1
            time.sleep(PAUSE_S)
        if n % 10 == 0:
            log.warning("rivers %d/%d stations %s", n, len(stations), counts)
    return counts


def download_dams(out: Path, dam_ids: list[int], years: list[int], client: httpx.Client) -> dict:
    counts = {"fetched": 0, "skipped": 0, "failed": 0}
    for dam_id in dam_ids:
        for series in DAM_SERIES:
            path = out / "dams" / f"{dam_id}_{series}.json.gz"
            if path.exists():
                counts["skipped"] += 1
                continue
            params = {"data_type": series, "dam_id": dam_id, "year": ",".join(map(str, years))}
            try:
                response = client.get(f"{THAIWATER_ROOT}/analyst/dam_yearly_graph", params=params, timeout=120)
                response.raise_for_status()
                _write_gz(path, {"dam_id": dam_id, "series": series, "raw": response.json()})
                counts["fetched"] += 1
            except (httpx.HTTPError, ValueError) as exc:
                log.warning("dam %s %s: %s", dam_id, series, exc)
                counts["failed"] += 1
            time.sleep(PAUSE_S)
    return counts


def dam_ids(client: httpx.Client) -> list[dict]:
    """The 35 large dams from the `thailand_main` bundle (id and name)."""
    response = client.get(f"{THAIWATER_ROOT}/public/thailand_main", timeout=90)
    response.raise_for_status()
    data = ((response.json() or {}).get("dam") or {}).get("data") or {}
    rows = data.get("data", []) if isinstance(data, dict) else data
    dams = []
    for row in rows:
        dam = row.get("dam") or {}
        if dam.get("id") is not None:
            dams.append({"id": int(dam["id"]), "name_th": (dam.get("dam_name") or {}).get("th"),
                         "lat": dam.get("dam_lat"), "lon": dam.get("dam_long")})
    return dams


OPEN_METEO_ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
RAIN_CELL_DEG = 0.5


def rain_cell(lat: float, lon: float) -> tuple[float, float]:
    """The 0.5° cell centre a station's rain is read from (ERA5 itself is 0.25°)."""
    return round(round(lat / RAIN_CELL_DEG) * RAIN_CELL_DEG, 2), round(round(lon / RAIN_CELL_DEG) * RAIN_CELL_DEG, 2)


def download_rain(out: Path, stations: list[dict], start: date, end: date, client: httpx.Client) -> dict:
    """Hourly rain (mm, ERA5 via the Open-Meteo archive, Thai local time) for every station's 0.5° cell.

    Measured on 2026-11-05: one point, one variable, one year of hours costs 2.6 weighted calls, so five years for
    ~130 cells is ~1,700 of the free tier's 10,000 a day, which the live app's forecasts also use from this machine.
    On a 429 the run waits 65 s; on a daily or hourly limit it stops (a later run resumes).
    """
    counts = {"fetched": 0, "skipped": 0, "failed": 0, "cost": 0.0}
    cells = sorted({rain_cell(s["lat"], s["lon"]) for s in stations})
    for lat, lon in cells:
        path = out / "rain" / f"{lat:.2f}_{lon:.2f}.json.gz"
        if path.exists():
            counts["skipped"] += 1
            continue
        params = {"latitude": lat, "longitude": lon, "start_date": start.isoformat(), "end_date": end.isoformat(),
                  "hourly": "precipitation", "timezone": "Asia/Bangkok"}
        for attempt in range(3):
            response = client.get(OPEN_METEO_ARCHIVE, params=params, timeout=120)
            if response.status_code == 429:
                reason = response.text
                if "Daily" in reason or "Hourly" in reason:
                    log.warning("Open-Meteo limit reached, stopping: %s", reason)
                    counts["stopped"] = reason
                    return counts
                time.sleep(65)
                continue
            if response.status_code != 200:
                log.warning("rain %s,%s: HTTP %s %s", lat, lon, response.status_code, response.text[:200])
                break
            hourly = response.json()["hourly"]
            _write_gz(path, {"lat": lat, "lon": lon, "time": hourly["time"], "precipitation": hourly["precipitation"]})
            counts["fetched"] += 1
            counts["cost"] += float(response.headers.get("X-RateLimit-Cost", 0) or 0)
            break
        else:
            counts["failed"] += 1
        time.sleep(1.0)
    return counts


def client() -> httpx.Client:
    return httpx.Client(headers=USER_AGENT, follow_redirects=True)
