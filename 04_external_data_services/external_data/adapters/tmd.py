import asyncio
import logging
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from external_data.adapters.base import Adapter, parse_local, to_float
from thanphai_shared import HazardType, OfficialWarning, SourceRecord

log = logging.getLogger(__name__)

BASE_URL = "https://data.tmd.go.th/api"
ATTRIBUTION = "ที่มา: กรมอุตุนิยมวิทยา (TMD)"

# Keywords in the Thai warning title that map to our hazards. A warning can match several hazards.
HAZARD_KEYWORDS = {
    HazardType.RAIN_STORM: ("ฝนตกหนัก", "ฝนหนัก", "พายุ", "คลื่นลมแรง", "ดีเปรสชัน", "ไต้ฝุ่น"),
    HazardType.FLOOD: ("น้ำท่วม", "น้ำป่า", "น้ำล้นตลิ่ง", "ฝนตกหนักถึงหนักมาก"),
    HazardType.DROUGHT: ("ภัยแล้ง", "ฝนทิ้งช่วง"),
    HazardType.FIRE: ("ไฟป่า", "หมอกควัน"),
    HazardType.AIR_QUALITY: ("หมอกควัน", "ฝุ่นละออง", "PM2.5"),
}


def _credentials() -> dict[str, str]:
    # TMD publishes uid=api / ukey=api12345 as a public demo pair for its open services.
    return {"uid": os.getenv("TMD_UID", "api"), "ukey": os.getenv("TMD_UKEY", "api12345"), "format": "json"}


def classify_hazards(text: str) -> list[HazardType]:
    return [hazard for hazard, words in HAZARD_KEYWORDS.items() if any(word in text for word in words)]


# "… ฉบับที่ 3 (286/2569)": issues of one warning differ only in this tail.
ISSUE_TAIL = re.compile(r"\s*ฉบับที่\s*\d+\s*(?:\(\s*\d+\s*/\s*\d+\s*\))?\s*$")


THAI_MONTHS = {name: i for i, name in enumerate(
    ("มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม",
     "พฤศจิกายน", "ธันวาคม"), start=1)}
# Headlines use short forms: "ในช่วงวันที่ 18-20 ต.ค. 69".
SHORT_MONTHS = {name: i for i, name in enumerate(
    ("ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."), start=1)}
MONTHS = THAI_MONTHS | SHORT_MONTHS
_MONTH = "|".join(re.escape(name) for name in sorted(MONTHS, key=len, reverse=True))
_YEAR = r"(?:พ\.ศ\.\s*)?(\d{4}|\d{2}(?!\d))"
# In the title's "(มีผลกระทบ …)": "ในช่วงวันที่ 5-9 พฤศจิกายน 2569", "30 ตุลาคม - 2 พฤศจิกายน 2569", "จนถึงวันที่ 25 ตุลาคม 2569".
IMPACT_NOTE = re.compile(r"มีผลกระทบ([^)]*)")
DAY_RANGE = re.compile(rf"(\d{{1,2}})\s*(?:({_MONTH})\s*{_YEAR}?)?\s*[-–]\s*(\d{{1,2}})\s*({_MONTH})\s*{_YEAR}?")
ONE_DAY = re.compile(rf"(\d{{1,2}})\s*({_MONTH})\s*{_YEAR}?")


def _date(day: str, month: int, year: str | None, announced: date) -> date:
    """A Thai date: Buddhist year "2569" or "69"; with no year, the one closest to the issue day. A warning issued
    on 30 Dec 2026 that says "2 ม.ค." means 2 Jan 2027, not 2 Jan 2026 (which would already be over)."""
    if year:
        return date((int(year) + 2500 if len(year) == 2 else int(year)) - 543, month, int(day))
    candidates = []
    for y in (announced.year - 1, announced.year, announced.year + 1):
        try:
            candidates.append(date(y, month, int(day)))
        except ValueError:  # 29 February in a common year
            pass
    best = min(candidates, key=lambda d: abs(d - announced), default=None)
    # Warnings speak of the days around their issue; half a year off means no such day ("29 ก.พ." in 2027).
    if best is None or abs(best - announced) > timedelta(days=183):
        raise ValueError(f"no such day near {announced}: {day}/{month}")
    return best


def read_days(text: str, announced: date) -> tuple[date, date] | None:
    """First and last day of the first date or date range in a warning text (Thai dates, Buddhist years); None if none."""
    try:
        if m := DAY_RANGE.search(text):
            d1, m1, y1, d2, m2, y2 = m.groups()
            end = _date(d2, MONTHS[m2], y2, announced)
            month = MONTHS[m1] if m1 else end.month
            # The first day takes its year from the last one: "30 ธันวาคม - 2 มกราคม 2570" starts in 2569.
            year = _date(d1, month, y1, announced).year if y1 else end.year - (month > end.month)
            return date(year, month, int(d1)), end
        if m := ONE_DAY.search(text):
            day = _date(m.group(1), MONTHS[m.group(2)], m.group(3), announced)
            return (min(announced, day), day) if "จนถึง" in text[:m.start()] else (day, day)
    except ValueError:  # an impossible date ("31 พฤศจิกายน")
        return None
    return None


def impact_window(title: str, announced: date) -> tuple[date, date] | None:
    """First and last impact day of the whole warning, from the title's "(มีผลกระทบ …)"; None when it gives none.

    EffectStartDate/EffectEndDate cannot be used: EffectEndDate is when the next issue is due.
    """
    note = IMPACT_NOTE.search(title)
    return read_days(note.group(1), announced) if note else None


def bulletin_body(page_text: str) -> str | None:
    """The statements of a TMD bulletin from its PDF page 1, or None when the page is not laid out as one.

    The API's HeadlineThai is the first paragraph only: issue 288/2569 said "ส่วนในช่วงวันที่ 7–9 … ภาคใต้" only in the
    PDF. The statements sit between the "-----" rule under the title and "ประกาศ ณ วันที่"; text from "ทั้งนี้" on is
    the cause ("ทั้งนี้เนื่องจาก … ประเทศไทยตอนบน") and general advice, which name areas without warning them.
    Lines are joined without a space, as Thai breaks lines inside sentences ("ประเทศไทย" / "ตอนบน").
    """
    lines = page_text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip().startswith("---")), None)
    end = next((i for i, line in enumerate(lines) if line.strip().startswith("ประกาศ ณ")), None)
    if start is None or end is None or end <= start:
        return None
    body = "".join(line.rstrip() for line in lines[start + 1:end]).split("ทั้งนี้")[0].strip()
    return body or None


def read_bulletin(pdf: bytes) -> str | None:
    from io import BytesIO

    from pypdf import PdfReader

    reader = PdfReader(BytesIO(pdf))
    return bulletin_body(reader.pages[0].extract_text()) if reader.pages else None


def latest_issues(warnings: list[OfficialWarning]) -> list[OfficialWarning]:
    """The newest issue of each warning, newest first. TMD's feed keeps earlier issues next to the one that replaced
    them (2026-11-02: issues 2 and 3 of the same warning)."""
    seen: set[str] = set()
    kept = []
    for w in sorted(warnings, key=lambda w: w.announced_at, reverse=True):
        subject = ISSUE_TAIL.sub("", w.title)
        if subject not in seen:
            seen.add(subject)
            kept.append(w)
    return kept


def as_list(value: Any) -> list:
    """TMD returns a single object when there is one item and a list when there are several."""
    if value is None or value == {}:
        return []
    return value if isinstance(value, list) else [value]


class TmdWarnings(Adapter):
    """Official weather warnings (WeatherWarningNews v2; v1 is stale)."""

    name = "tmd_warnings"
    attribution = ATTRIBUTION

    async def fetch(self, client: httpx.AsyncClient) -> Any:
        response = await client.get(f"{BASE_URL}/WeatherWarningNews/v2/", params=_credentials(), timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.json()

    def parse(self, raw: Any, fetched_at: datetime) -> list[SourceRecord]:
        # Warnings are not numeric records; use parse_warnings(). Kept for the Adapter interface.
        return []

    def parse_warnings(self, raw: Any, fetched_at: datetime) -> list[OfficialWarning]:
        fmt = "%Y-%m-%d %H:%M:%S"
        warnings = []
        for item in as_list(raw.get("Warning")):
            title = (item.get("TitleThai") or "").strip()
            headline = (item.get("HeadlineThai") or "").strip() or None
            announced = parse_local(item.get("AnnounceDate"), fmt)
            if not title or announced is None:
                continue
            warnings.append(
                OfficialWarning(
                    source=self.name,
                    issue_no=str(item.get("IssueNo", "")),
                    title=title,
                    headline=headline,
                    hazards=classify_hazards(f"{title} {headline or ''}"),
                    announced_at=announced,
                    effective_from=parse_local(item.get("EffectStartDate"), fmt),
                    effective_to=parse_local(item.get("EffectEndDate"), fmt),
                    url=item.get("WebUrlThai"),
                    fetched_at=fetched_at,
                    attribution=self.attribution,
                )
            )
        return warnings

    async def run_warnings(self, client: httpx.AsyncClient) -> list[OfficialWarning]:
        fetched_at = datetime.now(timezone.utc)
        warnings = self.parse_warnings(await self.fetch(client), fetched_at)
        # Only the newest issue of each warning is used; older issues in the feed keep the headline alone.
        latest = {id(w) for w in latest_issues(warnings)}
        bodies = await asyncio.gather(*(self.bulletin(client, w.url if id(w) in latest else None) for w in warnings))
        return [w.model_copy(update={"body": body}) for w, body in zip(warnings, bodies)]

    # PDF url -> statements. An issue's PDF never changes; a new issue has a new url.
    _bodies: dict[str, str | None] = {}

    async def bulletin(self, client: httpx.AsyncClient, url: str | None) -> str | None:
        """The bulletin's statements from its PDF; None (use the headline) when there is no PDF or it cannot be read."""
        if not url:
            return None
        if url not in self._bodies:
            try:
                response = await client.get(url, timeout=self.timeout_seconds)
                response.raise_for_status()
                TmdWarnings._bodies[url] = await asyncio.to_thread(read_bulletin, response.content)
            except Exception as exc:  # a PDF that fails now is tried again on the next refresh
                log.warning("TMD bulletin %s unreadable: %s", url, exc)
                return None
        return self._bodies[url]


class TmdWeather3Hours(Adapter):
    """Synoptic station observations every 3 hours."""

    name = "tmd_weather_3h"
    attribution = ATTRIBUTION
    variables = {
        "Rainfall": ("rain_3h", "mm"),
        "Rainfall24Hr": ("rain_24h", "mm"),
        "RelativeHumidity": ("relative_humidity", "%"),
        "AirTemperature": ("air_temperature", "°C"),
        "WindSpeed": ("wind_speed", "km/h"),
    }

    async def fetch(self, client: httpx.AsyncClient) -> Any:
        response = await client.get(f"{BASE_URL}/Weather3Hours/V2/", params=_credentials(), timeout=self.timeout_seconds)
        response.raise_for_status()
        return response.json()

    def parse(self, raw: Any, fetched_at: datetime) -> list[SourceRecord]:
        records = []
        for station in as_list((raw.get("Stations") or {}).get("Station")):
            obs = station.get("Observation") or {}
            observed_at = parse_local(obs.get("DateTime"), "%m/%d/%Y %H:%M:%S")
            for key, (variable, unit) in self.variables.items():
                records.append(
                    SourceRecord(
                        source=self.name,
                        variable=variable,
                        value=to_float(obs.get(key)),
                        unit=unit,
                        lat=to_float(station.get("Latitude")),
                        lon=to_float(station.get("Longitude")),
                        station_id=station.get("WmoStationNumber"),
                        observed_at=observed_at,
                        fetched_at=fetched_at,
                        attribution=self.attribution,
                        extra={"station_name_en": station.get("StationNameEnglish"), "province_th": station.get("Province")},
                    )
                )
        return records
