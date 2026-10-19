"""Weather images from ThaiWater's `thailand_main` bundle: rain radar, HII rain forecast, storm maps, SWAN waves.

Each item carries an opaque `media_path` token; the image itself is served by
`shared/image?image=<token>` (checked live 2026-10-19 for jpg, png, gif and mp4).
The bundle keeps the last image of every radar ever connected, some from 2017, so radar and storm
images older than a few hours are dropped instead of being shown as "latest".
Animations (16-26 MB mp4/gif) are left out; a phone should not download them by default.
"""

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from external_data.adapters.base import BANGKOK
from external_data.adapters.thaiwater import BASE_URL

MEDIA_URL = BASE_URL.rsplit("/", 1)[0] + "/shared/image?image="
TIME_FORMAT = "%Y-%m-%d %H:%M"
RADAR_MAX_AGE = timedelta(hours=3)
STORM_MAX_AGE = timedelta(days=2)
# Rain-forecast images are dated by the day they forecast; older than this means HII stopped updating.
FORECAST_MAX_AGE = timedelta(days=2)

# Thai place names for radar codes (TMD, BMA and the Royal Rainmaking department's radars).
RADAR_NAMES = {
    "cri": "เชียงราย", "lmp": "ลำพูน", "mhs": "แม่ฮ่องสอน", "nan": "น่าน", "phs": "พิษณุโลก",
    "phb": "เพชรบูรณ์", "kkn": "ขอนแก่น", "skn": "สกลนคร", "srn": "สุรินทร์", "ubn": "อุบลราชธานี",
    "chn": "ชัยนาท", "svp": "สุวรรณภูมิ", "skm": "สมุทรสงคราม", "ryg": "ระยอง", "hhn": "หัวหิน",
    "cmp": "ชุมพร", "srt": "สุราษฎร์ธานี", "krb": "กระบี่", "pkt": "ภูเก็ต", "stp": "สทิงพระ (สงขลา)",
    "nrt": "นราธิวาส", "njk": "หนองจอก (กทม.)", "nkm": "หนองแขม (กทม.)", "omkoi": "อมก๋อย (เชียงใหม่)",
    "inburi": "อินทร์บุรี (สิงห์บุรี)", "phimai": "พิมาย (นครราชสีมา)", "sattahip": "สัตหีบ (ชลบุรี)", "phanom": "พนม (สุราษฎร์ธานี)", "rongkwang": "ร้องกวาง (แพร่)",
    "rasisalai": "ราษีไศล (ศรีสะเกษ)", "banphue": "บ้านผือ (อุดรธานี)", "pathio": "ปะทิว (ชุมพร)", "takhli": "ตาคลี (นครสวรรค์)",
}
RADAR_AGENCIES = {"tmd": "กรมอุตุนิยมวิทยา", "bma": "กรุงเทพมหานคร", "drraa": "กรมฝนหลวงและการบินเกษตร"}
STORM_SOURCES = {
    "college": "Tropical Storm Risk (UCL)",
    "typhoon": "ภาพดาวเทียม Himawari (Digital Typhoon)",
    "us": "ภาพดาวเทียม Himawari (US Navy)",
    "kochi": "ภาพดาวเทียม (Kochi University)",
}
# Tropical Storm Risk track maps, one per ocean basin (checked by eye 2026-10-19).
STORM_REGIONS = {"I.png": "มหาสมุทรอินเดียเหนือ", "W.png": "มหาสมุทรแปซิฟิกตะวันตก"}
FORECAST_SETS = {
    "pre_rain": ("thailand", "ประเทศไทย"),
    "pre_rain_basin": ("basin", "รายลุ่มน้ำ"),
    "pre_rain_sea": ("sea", "เอเชียตะวันออกเฉียงใต้"),
    "pre_rain_asia": ("asia", "เอเชีย"),
}


def media_url(token: str | None) -> str | None:
    return MEDIA_URL + quote(token, safe="") if token else None


def _rows(section: Any) -> Any:
    """Sections are wrapped as {"data": {"result", "data"}} or {"result", "data"}."""
    if not isinstance(section, dict):
        return []
    inner = section.get("data", section)
    if isinstance(inner, dict) and "data" in inner:
        inner = inner["data"]
    return inner or []


def _time(value: str | None, tz=BANGKOK) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, TIME_FORMAT).replace(tzinfo=tz).astimezone(BANGKOK)
    except ValueError:
        return None


def _item(row: dict, when: datetime | None, **extra) -> dict | None:
    image = media_url(row.get("media_path"))
    if image is None:
        return None
    return {"image": image, "thumb": media_url(row.get("media_path_thumb")) or image,
            "time": when.isoformat() if when else None, **extra}


def radar_name(code: str) -> str:
    """'ubn120' -> 'อุบลราชธานี (รัศมี 120 กม.)'."""
    base = code.rstrip("0123456789")
    radius = code[len(base):]
    name = RADAR_NAMES.get(base, base)
    return f"{name} (รัศมี {radius} กม.)" if radius else name


def parse_media(raw: Any, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    raw = raw if isinstance(raw, dict) else {}

    radar = []
    for row in _rows(raw.get("radar")):
        # All radar times are UTC. BMA rows say timezone "TST", but the image itself reads 17:55 Thai time
        # for media_datetime 10:55 (checked 2026-10-19), so the label is wrong, not the time.
        when = _time(row.get("media_datetime"), timezone.utc)
        if when is None or now - when > RADAR_MAX_AGE or when - now > timedelta(hours=1):
            continue
        code = row.get("radar_type") or ""
        item = _item(row, when, id=code, name_th=radar_name(code),
                     agency_th=RADAR_AGENCIES.get(row.get("agency"), row.get("agency")))
        if item:
            radar.append(item)
    radar.sort(key=lambda r: r["name_th"])

    forecast = {}
    for section, (key, label) in FORECAST_SETS.items():
        days = []
        for row in _rows(raw.get(section)):
            when = _time(row.get("media_datetime"))
            if when is None:
                continue
            item = _item(row, when, date=when.date().isoformat())
            if item:
                days.append(item)
        days.sort(key=lambda d: d["date"])
        stale = not days or now - datetime.fromisoformat(days[-1]["time"]) > FORECAST_MAX_AGE
        forecast[key] = {"label_th": label, "days": [] if stale else days}

    storm = []
    storm_rows = _rows(raw.get("storm"))
    for source, rows in (storm_rows.items() if isinstance(storm_rows, dict) else []):
        for row in rows or []:
            when = _time(row.get("media_datetime"))
            if when is None or now - when > STORM_MAX_AGE:
                continue
            item = _item(row, when, source=source, source_th=STORM_SOURCES.get(source, source),
                         region_th=STORM_REGIONS.get(row.get("filename") or "", "ภาพเมฆจากดาวเทียม"))
            if item:
                storm.append(item)

    wave = []
    for row in _rows(raw.get("wave")):
        when = _time(row.get("media_datetime"))
        item = _item(row, when)
        if item and when is not None:
            wave.append(item)
    wave.sort(key=lambda w: w["time"])
    if wave and now - datetime.fromisoformat(wave[-1]["time"]) > FORECAST_MAX_AGE:
        wave = []

    return {"radar": radar, "rain_forecast": forecast, "storm": storm, "wave": wave}
