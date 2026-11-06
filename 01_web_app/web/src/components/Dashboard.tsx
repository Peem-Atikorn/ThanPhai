"use client";

import { t as tr, getLocale } from "@/lib/i18n";
import dynamic from "next/dynamic";
import { createPortal } from "react-dom";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { AirStation, api, AreaBoundary, AreaHazards, AskAnswer, Dam, damBadge, damColor, describeObservation, groupOf, GROUPS, HAZARDS, itemTh, levelInfo, LEVELS, MapItem, MapLayerId, MapView, OfficialWarning, RadarFrame, RadarNowcast, WaterQualityStation, mcmPerDayToM3s, PM25_BANDS, pm25Band, River, riverColor, RIVER_STATUS, Shelter, SHELTER_COLOR, ShelterRoute } from "@/lib/api";
import WaterQualityPanel, { qualityKey, qualityColor } from "@/components/WaterQualityPanel";
import Icon from "@/components/Icon";
import FloatingMascot from "@/components/FloatingMascot";
import RainCloudSkeleton from "@/components/RainCloudSkeleton";
import DamHistoryChart from "@/components/DamHistoryChart";
import MediaPanel from "@/components/MediaPanel";
import EventsPanel from "@/components/EventsPanel";
import EnvironmentSummary, { ENVIRONMENT_METRICS, type EnvironmentState, type Metric as EnvironmentMetric } from "@/components/EnvironmentSummary";
import StormsPanel from "@/components/StormsPanel";
import AreaWarnings from "@/components/AreaWarnings";
import { NearestShelters, ShelterCard } from "@/components/ShelterCard";
import RiskAlerts from "@/components/RiskAlerts";
import RiskSnapshot, { type RiskFilter } from "@/components/RiskSnapshot";
import RadarPlayback, { radarImageFailed, radarImageLoaded } from "@/components/RadarPlayback";
import AnswerText from "@/components/AnswerText";
import { riskAreasAtLevel } from "@/lib/risk-overview";
import { OverlayControls, OverlayLegends, useOverviewLayers } from "@/components/OverviewLayers";
import { RiverDetail, RiverRow, riverTime, RiverSort, sortRivers } from "@/components/RiverPanel";
// Right after a backend restart the first requests can fail for a few seconds; try again quietly
// (after 3 s, then 6 s) before the map shows a load error.
function withRetry<T>(load: () => Promise<T>, tries = 3, wait = 3000): Promise<T> {
    return load().catch((error) => tries > 1
        ? new Promise<void>((resolve) => setTimeout(resolve, wait)).then(() => withRetry(load, tries - 1, wait * 2))
        : Promise.reject(error));
}
const HazardMap = dynamic(() => import("@/components/HazardMap"), {
    ssr: false,
    loading: () => <RainCloudSkeleton inline/>,
});
type Layer = Awaited<ReturnType<typeof api.layer>>;
// PM2.5, heat and rain-chance layers feed the summary cards as well as their own map tab.
const isEnvironment = (id: string): id is EnvironmentMetric => (ENVIRONMENT_METRICS as readonly string[]).includes(id);
type SearchArea = Awaited<ReturnType<typeof api.search>>[number];
const number = new Intl.NumberFormat(getLocale());
const prefix = (level: string) => level === "province" ? "จ." : level === "tambon" ? "ต." : "อ.";
const hazardDescriptions: Record<MapLayerId, string> = {
    overview: "สีของแต่ละอำเภอคือระดับสูงสุดของทุกภัย แตะอำเภอเพื่อดูว่าเป็นภัยอะไร",
    flood: "ระดับน้ำและพื้นที่น้ำท่วมจากดาวเทียม",
    rain_storm: "ปริมาณฝนและสภาพอากาศรายพื้นที่",
    fire: "จุดความร้อนและโอกาสเกิดไฟในพื้นที่",
    drought: "ความแห้งแล้งและความสมบูรณ์ของพืชพรรณ",
    air_quality: "ค่าฝุ่น PM2.5 เฉลี่ย 24 ชม. จากสถานีวัดและแบบจำลอง",
    heat: "ดัชนีความร้อนสูงสุดใน 24 ชม. ข้างหน้าจากแบบจำลองพยากรณ์อากาศ",
    earthquake: "แรงสั่นที่ประเมินจากแผ่นดินไหวใน 24 ชม. ที่ผ่านมา (กรมอุตุฯ และ USGS)",
    marine: "ความสูงคลื่นสูงสุดใน 24 ชม. ข้างหน้า เฉพาะอำเภอติดทะเล (อำเภออื่นแสดงเป็นไม่มีข้อมูล)",
    tropical_cyclone: "รัศมีลมแรงของพายุตามเส้นทางปัจจุบันและคาดการณ์ (GDACS)",
    landslide: "ฝนหนักในพื้นที่ที่เคยเกิดดินถล่ม (กรมทรัพยากรธรณี) อำเภออื่นแสดงเป็นไม่มีข้อมูล",
};
const areaIntroductions = (): Record<MapLayerId, {
    title: string;
    description: string;
}> => ({
    overview: { title: "ภัยทุกประเภทในพื้นที่ของคุณ", description: tr("แตะอำเภอบนแผนที่ หรือค้นหาชื่อพื้นที่ เพื่อดูความเสี่ยงทั้ง ") + tr(HAZARDS.length) + tr(" ประเภท") },
    flood: { title: "ความเสี่ยงน้ำท่วมในพื้นที่ของคุณ", description: "แตะอำเภอบนแผนที่ หรือค้นหาชื่อพื้นที่ เพื่อดูระดับความเสี่ยงน้ำท่วมและข้อมูลระดับน้ำ" },
    rain_storm: { title: "ฝนหนักและพายุในพื้นที่ของคุณ", description: "แตะอำเภอบนแผนที่ หรือค้นหาชื่อพื้นที่ เพื่อดูความเสี่ยงฝนหนักและพายุ พร้อมพยากรณ์ฝน" },
    fire: { title: "ความเสี่ยงไฟป่าในพื้นที่ของคุณ", description: "แตะอำเภอบนแผนที่ หรือค้นหาชื่อพื้นที่ เพื่อดูจุดความร้อนและความเสี่ยงไฟป่า" },
    drought: { title: "ความเสี่ยงภัยแล้งในพื้นที่ของคุณ", description: "แตะอำเภอบนแผนที่ หรือค้นหาชื่อพื้นที่ เพื่อดูความเสี่ยงภัยแล้งและข้อมูลความแห้งแล้ง" },
    air_quality: { title: "ฝุ่น PM2.5 ในพื้นที่ของคุณ", description: "แตะอำเภอบนแผนที่ หรือค้นหาชื่อพื้นที่ เพื่อดูค่าฝุ่นเฉลี่ย 24 ชม. และสถานีวัดใกล้เคียง" },
    heat: { title: "ความร้อนจัดในพื้นที่ของคุณ", description: "แตะอำเภอบนแผนที่ หรือค้นหาชื่อพื้นที่ เพื่อดูดัชนีความร้อนที่คาดการณ์" },
    earthquake: { title: "แผ่นดินไหวใกล้พื้นที่ของคุณ", description: "แตะอำเภอบนแผนที่ หรือค้นหาชื่อพื้นที่ เพื่อดูแรงสั่นจากแผ่นดินไหวที่ผ่านมา" },
    marine: { title: "คลื่นลมแรงตามชายฝั่งของคุณ", description: "แตะอำเภอติดทะเลบนแผนที่ หรือค้นหาชื่อพื้นที่ เพื่อดูความสูงคลื่นที่คาดการณ์" },
    tropical_cyclone: { title: "พายุหมุนเขตร้อนใกล้พื้นที่ของคุณ", description: "แตะอำเภอบนแผนที่ หรือค้นหาชื่อพื้นที่ เพื่อดูว่าอยู่ในรัศมีลมแรงของพายุหรือไม่" },
    landslide: { title: "ความเสี่ยงดินถล่มในพื้นที่ของคุณ", description: "แตะอำเภอบนแผนที่ หรือค้นหาชื่อพื้นที่ เพื่อดูฝนหนักในพื้นที่ที่เคยเกิดดินถล่ม" },
});
function formatTime(iso: string | null | undefined) {
    if (!iso || Number.isNaN(new Date(iso).getTime()))
        return "ยังไม่มีข้อมูล";
    return new Date(iso).toLocaleString(getLocale(), { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Bangkok" });
}
// "5–7 พ.ย." for a bulletin's impact days (ISO dates, no time).
function dayRange(from: string | null, to: string | null) {
    const day = (iso: string, month: boolean) => new Date(iso + "T00:00:00+07:00").toLocaleDateString(getLocale(), month ? { day: "numeric", month: "short", timeZone: "Asia/Bangkok" } : { day: "numeric", timeZone: "Asia/Bangkok" });
    if (!from || !to)
        return "ตามประกาศ";
    return from === to ? day(from, true) : from.slice(0, 7) === to.slice(0, 7) ? day(from, false) + "–" + day(to, true) : day(from, true) + " – " + day(to, true);
}
const DAM_LEGEND: {
    th: string;
    color: string;
    match: (d: Dam) => boolean;
}[] = [
    { th: "เกินระดับกักเก็บปกติ", color: "#ad2337", match: (d) => d.status === "HIGH" && (d.storage_percent ?? 0) > 100 },
    { th: "ใกล้เต็มอ่าง", color: "#e07b16", match: (d) => d.status === "HIGH" && (d.storage_percent ?? 0) <= 100 },
    { th: "น้ำปกติ", color: "#2f7fb5", match: (d) => d.status === "NORMAL" },
    { th: "น้ำใช้การได้เหลือน้อย", color: "#d4a600", match: (d) => d.status === "LOW_CRITICAL" },
    { th: "ไม่มีข้อมูล", color: "#8a97a3", match: (d) => d.status === "UNKNOWN" },
];
const damKey = (d: Dam) => d.id ?? d.name_th;
const mcm = (v: number | null, digits = 2) => v == null ? "—" : v.toLocaleString(getLocale(), { minimumFractionDigits: digits, maximumFractionDigits: digits });
const m3s = (v: number | null) => v == null ? null : number.format(Math.round(mcmPerDayToM3s(v)));
/** Linear interpolation through (x, y) stops, for point sizes on the map. */
function scale(value: number, stops: [
    number,
    number
][]) {
    if (value <= stops[0][0])
        return stops[0][1];
    for (let i = 1; i < stops.length; i++) {
        const [x0, y0] = stops[i - 1];
        const [x1, y1] = stops[i];
        if (value <= x1)
            return y0 + ((value - x0) / (x1 - x0)) * (y1 - y0);
    }
    return stops[stops.length - 1][1];
}
// Circle area follows the water: sqrt of reservoir storage (million m³) or river flow (m³/s).
const damRadius = (d: Dam) => scale(Math.sqrt(d.storage_mcm ?? 0), [[0, 6], [40, 11], [135, 20]]);
const riverRadius = (r: River) => scale(Math.sqrt(r.discharge_m3s ?? 0), [[0, 4], [10, 6], [45, 15]]);
const riverKey = (r: River) => r.id ?? r.code;
const CHAO_PHRAYA_DAM_STATION = "C.13";
const CHAO_PHRAYA_DAM_ID = "chao-phraya-dam";
type WaterResult = { key: string; title: string; detail: string } & ({ kind: "dam"; dam: Dam } | { kind: "station"; river: River });

/** Dams and river stations found by the area search; each opens in its own view. */
function WaterResults({ items, onOpen }: { items: WaterResult[]; onOpen: (item: WaterResult) => void }) {
    return <div className="search-results water-results">
        <div className="results-heading"><span>{tr("เขื่อนและสถานีวัดน้ำ · ")}{tr(items.length)}{tr(" รายการ")}</span></div>
        <ul aria-label={tr("เขื่อนและสถานีวัดน้ำ")}>{items.map((item) => <li key={item.key}>
            <button type="button" onClick={() => onOpen(item)}>
                <Icon name={item.kind === "dam" || item.key === CHAO_PHRAYA_DAM_ID ? "dam" : "river"}/>
                <span><strong>{tr(item.title)}</strong><small>{tr(item.detail)}</small></span><Icon name="arrow"/>
            </button>
        </li>)}</ul>
    </div>;
}
const RID_HOURLY = "https://hyd-app.rid.go.th/hydro5h.html";
type View = MapView;
// The rain radar is drawn on these maps.
const RADAR_LAYERS: MapLayerId[] = ["overview", "rain_storm", "tropical_cyclone"];
// Asks once more for the Qwen text the backend was still writing (its Qwen timeout is 15 s).
const NOWCAST_RETRY_MS = 15_000;
// Draws the positions the backend projected for this lead time; a track only when the backend says the rain moves.
function forecastCellLayer(nowcast: RadarNowcast | null, horizonMin: number | undefined): GeoJSON.FeatureCollection {
    const frame = nowcast?.forecast_frames.find((f) => f.horizon_min === horizonMin);
    const features: GeoJSON.Feature[] = [];
    for (const position of frame?.cells ?? []) {
        const cell = nowcast?.rain_cells.find((c) => c.id === position.id);
        if (!cell) continue;
        const end: [number, number] = [position.lon, position.lat];
        const properties = { cellId: cell.id, intensityDbz: cell.peak_intensity_dbz };
        if (nowcast?.motion?.is_moving)
            features.push({ type: "Feature", geometry: { type: "LineString", coordinates: [[cell.centroid_lon, cell.centroid_lat], end] }, properties: { ...properties, kind: "track" } });
        features.push({ type: "Feature", geometry: { type: "Point", coordinates: end }, properties: { ...properties, kind: "position" } });
    }
    return { type: "FeatureCollection", features };
}
// One timeline: the newer of the two observed lists, then forecasts only when they start from its last frame.
function radarTimeline(observedRadar: { frames: RadarFrame[] } | null, nowcast: RadarNowcast | null) {
    const fromRadar = observedRadar?.frames ?? [];
    const fromNowcast: RadarFrame[] = (nowcast?.past_frames ?? []).map((frame) => ({ tiles: frame.tile_url, time: frame.time, kind: "observed" }));
    const observed = (fromNowcast.at(-1)?.time ?? -Infinity) > (fromRadar.at(-1)?.time ?? -Infinity) ? fromNowcast : fromRadar;
    const latest = observed.at(-1);
    const forecasts: RadarFrame[] = latest && nowcast?.motion && nowcast.past_frames.at(-1)?.time === latest.time
        ? nowcast.forecast_frames.map((frame) => ({ tiles: latest.tiles, imageTime: latest.time, time: frame.time, kind: "forecast", horizonMin: frame.horizon_min, shift: { dx: frame.dx_pixels, dy: frame.dy_pixels } }))
        : [];
    return { frames: [...observed, ...forecasts], latestObservedIndex: Math.max(0, observed.length - 1) };
}
const stationFeatures = (stations: AirStation[]): GeoJSON.FeatureCollection => ({
    type: "FeatureCollection",
    features: stations.map((s) => ({
        type: "Feature", geometry: { type: "Point", coordinates: [s.lon, s.lat] },
        properties: { pm25: s.pm25, title: tr("") + tr(s.name_th ?? "สถานี") + tr(": PM2.5 ") + tr(s.pm25.toFixed(1)) + tr(" มคก./ลบ.ม. (") + tr(pm25Band(s.pm25).th) + tr(")") },
    })),
});
// Hazards whose area card lists shelters; a WARNING or CRITICAL in one of them puts the list forward.
const SHELTER_HAZARDS = new Set<string>(["overview", "flood", "rain_storm", "landslide", "tropical_cyclone"]);
const shelterFeatures = (shelters: Shelter[]): GeoJSON.FeatureCollection => ({
    type: "FeatureCollection",
    features: shelters.map((s) => {
        const services = [s.electricity && tr("ไฟฟ้า"), s.water && tr("ประปา"), s.toilets && tr("ห้องน้ำ ") + s.toilets + tr(" ห้อง")].filter(Boolean);
        const detail = [
            s.responsible && s.responsible !== s.name ? tr("ดูแลโดย ") + s.responsible : "",
            [/^\d+$/.test(s.village) ? tr("ม.") + s.village : s.village, s.tambon && tr("ต.") + s.tambon, s.amphoe && tr("อ.") + s.amphoe, s.province && tr("จ.") + s.province].filter(Boolean).join(" "),
            s.capacity ? tr("รองรับ ") + number.format(s.capacity) + tr(" คน") : "",
            services.length ? tr("มี ") + services.join(" · ") : "",
            s.approximate ? tr("ตำแหน่งบนแผนที่เป็นค่าโดยประมาณ") : "",
        ].filter(Boolean).join("\n");
        return { type: "Feature", geometry: { type: "Point", coordinates: [s.lon, s.lat] }, properties: { title: s.name, detail, approximate: s.approximate } };
    }),
});
// Map look, remembered per browser. Storage can be blocked (private windows): then the flag still works for this
// page through `memory`, it is just not remembered. The server render uses the same default.
const memory = new Map<string, boolean>();
const flagListeners = new Set<() => void>();
function readFlag(key: string, fallback: boolean): boolean {
    try {
        const stored = window.localStorage.getItem(key);
        return stored === null ? fallback : stored === "1";
    }
    catch {
        return memory.get(key) ?? fallback;
    }
}
function useStoredFlag(key: string, fallback = false): [
    boolean,
    (on: boolean) => void
] {
    const value = useSyncExternalStore((notify) => { flagListeners.add(notify); return () => { flagListeners.delete(notify); }; }, () => readFlag(key, fallback), () => fallback);
    const set = useCallback((on: boolean) => {
        memory.set(key, on);
        try {
            window.localStorage.setItem(key, on ? "1" : "0");
        }
        catch { /* kept in memory only */ }
        flagListeners.forEach((notify) => notify());
    }, [key]);
    return [value, set];
}
const damDate = (d: string | null) => d ? new Date(d).toLocaleDateString(getLocale(), { dateStyle: "medium", timeZone: "Asia/Bangkok" }) : "—";
function DamRow({ dam, active, sortBy, onSelect }: {
    dam: Dam;
    active: boolean;
    sortBy: "storage" | "release";
    onSelect: (d: Dam) => void;
}) {
    const pct = dam.storage_percent;
    return (<li>
      <button type="button" className={"dam-row " + (active ? "is-active" : "")} aria-pressed={active} onClick={() => onSelect(dam)}>
        <span className="dam-name"><strong>{tr("เขื่อน")}{tr(dam.name_th)}</strong></span>
        <span className="dam-value">{tr(sortBy === "release"
            ? <><strong>{tr(mcm(dam.released_mcm))}</strong><small>{tr("ล้าน ลบ.ม./วัน")}</small></>
            : <><strong>{tr(pct != null ? pct.toFixed(0) + "%" : "—")}</strong><small>{tr("ของระดับกักเก็บปกติ")}</small></>)}
        </span>
        <span className="dam-meta"><span>{tr(dam.province_th ? "จ." + dam.province_th : "ไม่ระบุจังหวัด")}</span>{tr(dam.storage_mcm != null && <span>{tr("มีน้ำ ")}{tr(number.format(Math.round(dam.storage_mcm)))}{tr(" ล้าน ลบ.ม.")}</span>)}{tr(sortBy === "release" && pct != null && <span>{tr("น้ำในอ่าง ")}{tr(pct.toFixed(0))}%</span>)}</span>
        <span className={"risk-badge dam-row-status " + damBadge(dam)}>{tr(dam.status_th)}</span>
        <span className="dam-bar" aria-hidden><span style={{ width: Math.min(pct ?? 0, 120) / 1.2 + "%", background: damColor(dam) }}/></span>
      </button>
    </li>);
}
function DamDetail({ dam, onClose }: {
    dam: Dam;
    onClose: () => void;
}) {
    const flows: {
        label: string;
        value: number | null;
        note?: string;
    }[] = [
        { label: "ระบายออกวันนี้", value: dam.released_mcm },
        { label: "น้ำไหลเข้าวันนี้", value: dam.inflow_mcm },
        { label: "ระบายผ่านทางน้ำล้น", value: dam.spilled_mcm },
    ];
    return (<div className="dam-detail">
      <div className="area-title">
        <span className="area-icon" style={{ color: damColor(dam) }}><Icon name="dam"/></span>
        <div><h2>{tr("เขื่อน")}{tr(dam.name_th)}</h2><p>{tr("จ.")}{tr(dam.province_th)}{tr(dam.basin_th && <> · {tr(dam.basin_th.startsWith("ลุ่มน้ำ") ? dam.basin_th : "ลุ่มน้ำ" + dam.basin_th)}</>)}</p></div>
        <button className="icon-button" aria-label={tr("ปิดรายละเอียดเขื่อน")} onClick={onClose}><Icon name="close"/></button>
      </div>
      <div className="dam-storage">
        <div><strong>{tr(dam.storage_percent != null ? dam.storage_percent.toFixed(0) + "%" : "—")}</strong><span className={"risk-badge " + damBadge(dam)}>{tr(dam.status_th)}</span></div>
        <span className="dam-bar" aria-hidden><span style={{ width: Math.min(dam.storage_percent ?? 0, 120) / 1.2 + "%", background: damColor(dam) }}/></span>
        <p>{tr("มีน้ำ ")}{tr(mcm(dam.storage_mcm, 0))}{tr(" จากระดับกักเก็บปกติ ")}{tr(mcm(dam.normal_storage_mcm, 0))}{tr(" ล้าน ลบ.ม.")}{tr(dam.max_storage_mcm != null && <>{tr(" (รับได้สูงสุด ")}{tr(mcm(dam.max_storage_mcm, 0))})</>)}</p>
        {tr(dam.usable_percent != null && <p>{tr("น้ำที่นำมาใช้ได้ ")}{tr(dam.usable_percent.toFixed(0))}%</p>)}
      </div>
      <ul className="dam-flows">
        {tr(flows.map((f) => <li key={f.label}><span>{tr(f.label)}</span><strong>{tr(mcm(f.value))}<small>{tr(" ล้าน ลบ.ม.")}</small></strong>{tr(f.value != null && f.value > 0 && <small>{tr("เฉลี่ยราว ")}{tr(m3s(f.value))}{tr(" ลบ.ม./วินาที")}</small>)}</li>))}
      </ul>
      <div className="dam-totals">
        <h3>{tr("สะสมตั้งแต่ 1 มกราคม")}</h3>
        <p>{tr("น้ำไหลเข้า ")}{tr(mcm(dam.inflow_acc_mcm, 0))}{tr(" ล้าน ลบ.ม.")}{tr(dam.inflow_avg_year_mcm != null && dam.inflow_avg_year_mcm > 0 && dam.inflow_acc_mcm != null && <> ({tr(Math.round((dam.inflow_acc_mcm / dam.inflow_avg_year_mcm) * 100))}{tr("% ของค่าเฉลี่ยทั้งปี)")}</>)}</p>
        <p>{tr("ระบายออก ")}{tr(mcm(dam.released_acc_mcm, 0))}{tr(" ล้าน ลบ.ม.")}</p>
      </div>
      {tr(dam.id && <DamHistoryChart damId={dam.id}/>)}
      <p className="source-note">{tr("ข้อมูลรายวันของวันที่ ")}{tr(damDate(dam.date))}{tr(" \u00B7 ThaiWater (สสน.)")}{tr(dam.agency_th && <>{tr(", รายงานโดย ")}{tr(dam.agency_th)}</>)}</p>
      <p className="source-note">{tr("ปริมาณต่อวินาทีเป็นค่าเฉลี่ยจากยอดทั้งวัน ไม่ใช่อัตราระบาย ณ ขณะนี้")}</p>
    </div>);
}
export default function Dashboard({ activeHazard, onHazardChange, activeView, onViewChange, hideToolbar = false, searchInHeader = false, searchTarget, summaryTarget, headlineTarget, locationTarget, onOpenSearch, onAreaChosen, onReady, onDataTime, riskLevel, onRiskLevelChange }: {
    activeHazard?: MapLayerId;
    onHazardChange?: (value: MapLayerId) => void;
    activeView?: View;
    onViewChange?: (value: View) => void;
    hideToolbar?: boolean;
    searchInHeader?: boolean;
    searchTarget?: HTMLElement | null;
    summaryTarget?: HTMLElement | null;
    /** Where the one-line national summary goes (the page header). */
    headlineTarget?: HTMLElement | null;
    locationTarget?: HTMLElement | null;
    onOpenSearch?: () => void;
    onAreaChosen?: () => void;
    onReady?: () => void;
    /** When the data on the map was last fetched (ISO), for the footer clock. */
    onDataTime?: (updatedAt: string | null) => void;
    riskLevel?: RiskFilter | null;
    onRiskLevelChange?: (level: RiskFilter | null) => void;
}) {
    const [localHazard, setLocalHazard] = useState<MapLayerId>("overview");
    const hazard = activeHazard ?? localHazard;
    const setHazard = onHazardChange ?? setLocalHazard;
    const [revision, setRevision] = useState(0);
    const [environmentLayers, setEnvironmentLayers] = useState<Record<EnvironmentMetric, EnvironmentState | null>>({ air_quality: null, heat: null, rain_storm: null });
    const [layerState, setLayerState] = useState<{
        key: string;
        data: Layer | null;
        error: boolean;
    } | null>(null);
    const layerKey = hazard + ":" + revision;
    const activeLayerState = isEnvironment(hazard) ? environmentLayers[hazard] : layerState;
    const layer = activeLayerState?.key === layerKey || activeLayerState?.key.startsWith(hazard + ":") ? activeLayerState?.data ?? null : null;
    const layerError = activeLayerState?.key === layerKey && activeLayerState.error;
    const layerLoading = activeLayerState?.key !== layerKey;
    useEffect(() => { if (!layerLoading)
        onReady?.(); }, [layerLoading, onReady]);
    const dataTime = layer?.meta.data_updated_at ?? null;
    useEffect(() => { onDataTime?.(dataTime); }, [dataTime, onDataTime]);
    const [nationalLayer, setNationalLayer] = useState<Layer | null>(null);
    const needsNationalLayer = hazard !== "overview";
    useEffect(() => {
        if (!needsNationalLayer)
            return;
        let cancelled = false;
        api.layer("overview").then(data => !cancelled && setNationalLayer(data)).catch(() => !cancelled && setNationalLayer(null));
        return () => { cancelled = true; };
    }, [needsNationalLayer, revision]);
    const summaryLayer = needsNationalLayer ? nationalLayer : layer;
    const [selected, setSelected] = useState<string | null>(null);
    const [area, setArea] = useState<AreaHazards | null>(null);
    const [place, setPlace] = useState<AreaHazards["area"] | null>(null);
    const [areaStatus, setAreaStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
    const provinceCode = place?.code.slice(0, 4);
    const areaProvince = place ? { code: place.code.slice(0, 4), name: place.level === "province" ? place.name_th : place.parents.at(-1) ?? "พื้นที่ที่เลือก" } : null;
    const [focus, setFocus] = useState<{
        lat: number;
        lon: number;
        zoom?: number;
    } | null>(null);
    const [outline, setOutline] = useState<AreaBoundary | null>(null);
    const [areaHistory, setAreaHistory] = useState<{
        place: AreaHazards["area"] | null;
        selected: string | null;
        focus: { lat: number; lon: number; zoom?: number } | null;
        outline: AreaBoundary | null;
    }[]>([]);
    const outlineRequest = useRef(0);
    const [query, setQuery] = useState("");
    const [results, setResults] = useState<SearchArea[] | null>(null);
    const [searchStatus, setSearchStatus] = useState<"idle" | "loading" | "error" | "success">("idle");
    const searchRequest = useRef(0);
    const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
    const [searchActive, setSearchActive] = useState(false);
    const [activeSuggestion, setActiveSuggestion] = useState(-1);
    const locationRequest = useRef(0);
    const [locating, setLocating] = useState(false);
    const [locationError, setLocationError] = useState<string | null>(null);
    const searchInput = useRef<HTMLInputElement>(null);
    const [question, setQuestion] = useState("");
    // The conversation so far (newest last). `sent` is what went to the API: a short reply to a clarifying
    // question ("ธัญบุรี") is sent together with the question it answers.
    // The chat panel is a client-only portal (FloatingMascot), so reading sessionStorage here cannot cause a
    // hydration mismatch; the server simply starts with an empty log.
    const [chat, setChat] = useState<{ asked: string; sent: string; answer: AskAnswer | null }[]>(() => {
        if (typeof window === "undefined") return [];
        try {
            const saved = JSON.parse(sessionStorage.getItem("thanphai-chat") ?? "[]");
            return Array.isArray(saved) ? saved : [];
        } catch { return []; }
    });
    const answer = chat.at(-1)?.answer ?? null;
    const asking = chat.length > 0 && chat.at(-1)!.answer === null;
    const chatEnd = useRef<HTMLDivElement>(null);
    useEffect(() => {
        // Keep the last 20 answered turns for this browser tab only.
        try { sessionStorage.setItem("thanphai-chat", JSON.stringify(chat.filter(t => t.answer).slice(-20))); } catch { /* storage blocked */ }
        chatEnd.current?.scrollIntoView({ block: "nearest" });
    }, [chat]);
    const questionInput = useRef<HTMLInputElement>(null);
    const [dams, setDams] = useState<{
        list: Dam[];
        status: "loading" | "error" | "success";
    }>({ list: [], status: "loading" });
    const [localView, setLocalView] = useState<View>("hazard");
    const view = activeView ?? localView;
    const setView = onViewChange ?? setLocalView;
    const [rivers, setRivers] = useState<{
        list: River[];
        status: "loading" | "error" | "success";
    }>({ list: [], status: "loading" });
    const [quality, setQuality] = useState<{
        list: WaterQualityStation[];
        status: "loading" | "error" | "success";
        source: string;
        degraded: boolean;
        updated: string | null;
    }>({ list: [], status: "loading", source: "", degraded: false, updated: null });
    const [selectedQuality, setSelectedQuality] = useState<string | null>(null);
    const visibleQuality = useMemo(() => provinceCode ? quality.list.filter(s => s.province_code === provinceCode) : quality.list, [quality.list, provinceCode]);
    const visibleDams = useMemo(() => provinceCode ? dams.list.filter(d => d.province_code === provinceCode) : dams.list, [dams.list, provinceCode]);
    // เขื่อนเจ้าพระยา is a diversion dam with no reservoir, so it is not one of the 35 large dams; what it releases is
    // measured at C.13 below it. The dam view shows it with that reading and links to the station.
    const chaoPhrayaDam = useMemo(() => {
        const station = rivers.list.find((r) => r.code === CHAO_PHRAYA_DAM_STATION);
        return station && (!provinceCode || station.province_code === provinceCode) ? station : null;
    }, [rivers.list, provinceCode]);
    const qualityPoints = useMemo<GeoJSON.FeatureCollection>(() => ({ type: "FeatureCollection", features: visibleQuality.filter(s => s.lat != null && s.lon != null).map(s => ({ type: "Feature", geometry: { type: "Point", coordinates: [s.lon!, s.lat!] }, properties: { id: qualityKey(s), color: qualityColor(s), radius: 7 } })) }), [visibleQuality]);
    const [selectedRiver, setSelectedRiver] = useState<string | null>(null);
    const [riverSort, setRiverSort] = useState<RiverSort>("chain");
    const riverProvince = areaProvince;
    const visibleRivers = useMemo(() => riverProvince ? rivers.list.filter((r) => r.province_code === riverProvince.code) : rivers.list, [rivers.list, riverProvince]);
    const [airStations, setAirStations] = useState<AirStation[] | null>(null);
    const overview = view === "hazard" && hazard === "overview";
    const [localRiskLevel, setLocalRiskLevel] = useState<RiskFilter | null>(null);
    const selectedRiskLevel = overview ? (riskLevel === undefined ? localRiskLevel : riskLevel) : null;
    const overviewData = layer ?? nationalLayer;
    const riskAreas = useMemo(() => selectedRiskLevel ? riskAreasAtLevel(overviewData, selectedRiskLevel) : [], [overviewData, selectedRiskLevel]);
    const riskPoints = useMemo<GeoJSON.FeatureCollection>(() => ({ type: "FeatureCollection", features: riskAreas.map(a => ({ type: "Feature", geometry: { type: "Point", coordinates: a.center }, properties: { id: a.code, color: LEVELS.find(l => l.id === selectedRiskLevel)?.color, radius: 7 } })) }), [riskAreas, selectedRiskLevel]);
    const displayedLayer = useMemo(() => selectedRiskLevel && overviewData ? { ...overviewData, features: overviewData.features.filter(f => f.properties?.level === selectedRiskLevel) } : layer, [selectedRiskLevel, overviewData, layer]);
    const overlays = useOverviewLayers(overview, revision);
    const [pmOn, setPmOn] = useState(false);
    // DDPM shelters: a fixed list of ~10,000, fetched the first time the layer is turned on.
    const [shelterOn, setShelterOn] = useState(false);
    const [shelters, setShelters] = useState<{ list: Shelter[]; updated: string; status: "idle" | "loading" | "ok" | "error" }>({ list: [], updated: "", status: "idle" });
    const [shelterRoute, setShelterRoute] = useState<ShelterRoute | null>(null);
    const routeLine = useMemo(() => shelterRoute ? { line: shelterRoute.geometry, crosses: shelterRoute.flood.status === "crosses", water: shelterRoute.flood.area } : null, [shelterRoute]);
    const shelterLayer = useMemo(() => shelters.list.length ? shelterFeatures(shelters.list) : null, [shelters.list]);
    const overviewLegend = overview && !selectedRiskLevel && (pmOn || shelterOn || Object.values(overlays.enabled).some(Boolean));
    const [airStatus, setAirStatus] = useState<"loading" | "ok" | "error">("loading");
    const stationLayer = useMemo(() => (hazard === "air_quality" || (overview && pmOn)) && airStations ? stationFeatures(airStations) : null, [hazard, overview, pmOn, airStations]);
    const [radarOn, setRadarOn] = useState(false);
    // The image the map's hidden radar layer is fetching ahead (chosen by RadarPlayback within RainViewer's limit).
    const [radarPreload, setRadarPreload] = useState<string | null>(null);
    const radarImage = useCallback((tiles: string, ok: boolean) => {
        if (ok) { radarImageLoaded(tiles); return; }
        radarImageFailed(tiles);
        setRadarPreload((current) => current === tiles ? null : current);  // so the retry fetches it again
    }, []);
    // Each endpoint's last answer; the timeline and the selected frame are derived from them during render,
    // so the map never draws a frame that is not the selected one.
    const [radarObserved, setRadarObserved] = useState<{ enabled: boolean; frames: RadarFrame[] } | null>(null);
    const [radarNowcast, setRadarNowcast] = useState<RadarNowcast | null>(null);
    const [radarSelection, setRadarSelection] = useState<{ time: number; kind?: RadarFrame["kind"] } | null>(null);
    const radarAvailable = !!radarObserved?.enabled || !!radarNowcast?.enabled;
    const { frames: radarFrames, latestObservedIndex } = useMemo(() => radarTimeline(radarObserved, radarNowcast), [radarObserved, radarNowcast]);
    const selectedRadarIndex = radarFrames.findIndex((frame) => frame.time === radarSelection?.time && frame.kind === radarSelection?.kind);
    const radarFrameIndex = selectedRadarIndex >= 0 ? selectedRadarIndex : latestObservedIndex;
    const radarFrame = radarFrames[radarFrameIndex] ?? null;
    const radarForecast = useMemo(() => radarFrame?.kind === "forecast"
        ? forecastCellLayer(radarNowcast, radarFrame.horizonMin)
        : null, [radarFrame, radarNowcast]);
    const [darkMap, setDarkMap] = useStoredFlag("map-dark", true);
    const [riskOnly, setRiskOnly] = useStoredFlag("map-risk-only");
    const radarView = view === "hazard" && RADAR_LAYERS.includes(hazard);
    const showRadar = radarView && radarOn;
    // TMD bulletins for the overview, by region; a failed request just leaves the card out.
    const [nationalWarnings, setNationalWarnings] = useState<OfficialWarning[]>([]);
    useEffect(() => {
        if (!overview)
            return;
        let cancelled = false;
        api.warnings().then((d) => { if (!cancelled)
            setNationalWarnings(d.warnings); }).catch(() => { if (!cancelled)
            setNationalWarnings([]); });
        return () => { cancelled = true; };
    }, [overview, revision]);
    // The last item opened in each group, so its button goes back there.
    const lastInGroup = useRef<Record<string, MapItem>>({});
    const currentItem: MapItem = view === "hazard" ? hazard : view;
    const currentGroup = groupOf(currentItem);
    function openItem(item: MapItem) {
        lastInGroup.current[groupOf(item).id] = item;
        if (item === "water-quality")
            setView("water-quality");
        else if (item === "dams")
            setView("dams");
        else if (item === "rivers")
            setView("rivers");
        else {
            setHazard(item);
            setView("hazard");
        }
    }
    const [selectedDam, setSelectedDam] = useState<string | null>(null);
    const [damSort, setDamSort] = useState<"storage" | "release">("storage");
    const damPoints = useMemo<GeoJSON.FeatureCollection>(() => ({
        type: "FeatureCollection",
        features: [
            ...visibleDams.filter((d) => d.lat != null && d.lon != null).map((d): GeoJSON.Feature => ({
                type: "Feature",
                geometry: { type: "Point", coordinates: [d.lon!, d.lat!] },
                properties: { id: damKey(d), color: damColor(d), radius: damRadius(d) },
            })),
            ...(chaoPhrayaDam ? [{
                type: "Feature",
                geometry: { type: "Point", coordinates: [chaoPhrayaDam.lon, chaoPhrayaDam.lat] },
                properties: { id: CHAO_PHRAYA_DAM_ID, color: "#8a94a0", radius: 7 },
            } satisfies GeoJSON.Feature] : []),
        ],
    }), [visibleDams, chaoPhrayaDam]);
    const riverPoints = useMemo<GeoJSON.FeatureCollection>(() => ({
        type: "FeatureCollection",
        features: visibleRivers.map((r) => ({
            type: "Feature",
            geometry: { type: "Point", coordinates: [r.lon, r.lat] },
            properties: { id: riverKey(r), color: riverColor(r), radius: riverRadius(r) },
        })),
    }), [visibleRivers]);
    const riverDetail = visibleRivers.find((r) => riverKey(r) === selectedRiver) ?? null;
    // Within one province every station is listed (most provinces have a handful), not only the top 30.
    const sortedRivers = riverProvince && riverSort !== "overflow"
        ? [...visibleRivers].sort((a, b) => (b.discharge_m3s ?? -1) - (a.discharge_m3s ?? -1))
        : sortRivers(visibleRivers, riverSort);
    const belowChaoPhrayaDam = visibleRivers.find((r) => r.code === "C.13");
    const damDetail = visibleDams.find((d) => damKey(d) === selectedDam) ?? null;
    const sortedDams = damSort === "release" ? [...visibleDams].sort((a, b) => (b.released_mcm ?? -1) - (a.released_mcm ?? -1)) : visibleDams;
    const totalRelease = visibleDams.reduce((sum, d) => sum + (d.released_mcm ?? 0), 0);
    useEffect(() => {
        let cancelled = false;
        api.dams()
            .then((d) => !cancelled && setDams({ list: d.dams, status: "success" }))
            .catch(() => !cancelled && setDams((old) => ({ list: old.list, status: "error" })));
        return () => { cancelled = true; };
    }, [revision]);
    useEffect(() => {
        if (hazard !== "air_quality" && !overview)
            return;
        let cancelled = false;
        api.airStations().then((d) => { if (!cancelled) {
            setAirStations(d.stations);
            setAirStatus("ok");
        } }).catch(() => { if (!cancelled)
            setAirStatus("error"); });
        return () => { cancelled = true; };
    }, [hazard, overview, revision]);
    // Loaded when the layer is first turned on; after a failure, turning it on again retries.
    const toggleShelters = () => {
        const on = !shelterOn;
        setShelterOn(on);
        if (!on)
            setShelterRoute(null);
        if (!on || shelters.status === "ok" || shelters.status === "loading")
            return;
        setShelters((old) => ({ ...old, status: "loading" }));
        api.shelters()
            .then((d) => setShelters({ list: d.shelters, updated: d.updated, status: "ok" }))
            .catch(() => setShelters((old) => ({ ...old, status: "error" })));
    };
    // From the area card: the overview map with the shelter layer on, still at the open area.
    const showSheltersOnMap = () => {
        if (!overview)
            openItem("overview");
        if (!shelterOn)
            toggleShelters();
    };
    useEffect(() => {
        if (!radarView)
            return;
        let cancelled = false;
        let retry: ReturnType<typeof setTimeout> | undefined;
        // Observed tiles show as soon as the lightweight endpoint responds; nowcast/Qwen can finish later.
        // A failure of one endpoint leaves the other's frames in the timeline.
        api.radar().then((result) => { if (!cancelled) setRadarObserved(result); })
            .catch(() => { if (!cancelled) setRadarObserved({ enabled: false, frames: [] }); });
        const loadNowcast = (first: boolean) => api.radarNowcast().then((result) => {
            if (cancelled) return;
            setRadarNowcast(result);
            if (first && result.explanation_pending) retry = setTimeout(() => loadNowcast(false), NOWCAST_RETRY_MS);
        }).catch(() => { if (!cancelled && first) setRadarNowcast(null); });
        loadNowcast(true);
        return () => { cancelled = true; clearTimeout(retry); };
    }, [radarView, revision]);
    useEffect(() => {
        let cancelled = false;
        api.rivers()
            .then((d) => !cancelled && setRivers({ list: d.rivers, status: "success" }))
            .catch(() => !cancelled && setRivers((old) => ({ list: old.list, status: "error" })));
        return () => { cancelled = true; };
    }, [revision]);
    useEffect(() => {
        let cancelled = false;
        for (const metric of ENVIRONMENT_METRICS) {
            const key = metric + ":" + revision;
            withRetry(() => api.layer(metric))
                .then(data => !cancelled && setEnvironmentLayers(old => ({ ...old, [metric]: { key, data, error: false } })))
                .catch(() => !cancelled && setEnvironmentLayers(old => ({ ...old, [metric]: { key, data: null, error: true } })));
        }
        return () => { cancelled = true; };
    }, [revision]);
    useEffect(() => {
        // These layers also feed the persistent summary cards; switching tabs reuses that request.
        if (isEnvironment(hazard)) return;
        let cancelled = false;
        withRetry(() => api.layer(hazard))
            .then((data) => !cancelled && setLayerState({ key: layerKey, data, error: false }))
            .catch(() => !cancelled && setLayerState({ key: layerKey, data: null, error: true }));
        return () => { cancelled = true; };
    }, [hazard, layerKey]);
    // Live data changes through the day: reload every 10 minutes, and every 30 s while the backend is still
    // loading satellite/forecast data after a restart (otherwise the map kept the half-loaded first answer).
    const pending = !!layer?.meta.inputs_pending?.length;
    useEffect(() => {
        const id = window.setInterval(() => {
            if (document.visibilityState === "visible")
                setRevision((value) => value + 1);
        }, pending ? 30000 : 600000);
        return () => window.clearInterval(id);
    }, [pending]);
    useEffect(() => {
        if (view !== "water-quality")
            return;
        let cancelled = false;
        api.waterQuality()
            .then(d => !cancelled && setQuality({ list: d.stations, source: d.source, updated: d.data_updated_at, degraded: d.degraded_services.includes("thaiwater_waterquality"), status: "success" }))
            .catch(() => !cancelled && setQuality(old => ({ ...old, status: "error" })));
        return () => { cancelled = true; };
    }, [view, revision]);
    const latestArea = useRef(0);
    const select = useCallback((code: string, mapCode: string | null = code, context?: AreaHazards["area"], remember = true) => {
        if (remember && place?.code !== code)
            setAreaHistory(history => [...history, { place, selected, focus, outline }]);
        const request = ++latestArea.current;
        setPlace(previous => context ?? (previous?.code === code ? previous : { code, level: "amphoe", name_th: "กำลังโหลดพื้นที่…", name_en: "", parents: [] }));
        setSelected(mapCode);
        setArea(null);
        setAreaStatus("loading");
        api.area(code)
            .then((data) => {
            if (latestArea.current === request) {
                setArea(data);
                setPlace(data.area);
                setAreaStatus("success");
            }
        })
            .catch(() => latestArea.current === request && setAreaStatus("error"));
    }, [place, selected, focus, outline]);
    const selectedCode = useRef<string | null>(null);
    useEffect(() => {
        const code = selectedCode.current;
        if (!code) return;
        const request = ++latestArea.current;
        let cancelled = false;
        api.area(code).then(data => {
            if (!cancelled && latestArea.current === request) {
                setArea(data);
                setPlace(data.area);
                setAreaStatus("success");
            }
        }).catch(() => {
            if (!cancelled && latestArea.current === request) setAreaStatus("error");
        });
        return () => { cancelled = true; };
    }, [revision]);
    const selectFromMap = useCallback((code: string) => {
        outlineRequest.current++;
        setOutline(null); // a tapped district is outlined by the map's own selection layer
        selectedCode.current = code;
        select(code);
    }, [select]);
    function chooseArea(item: SearchArea | NonNullable<AskAnswer["area"]>, fromSearch = false) {
        setQuery("name_th" in item ? item.name_th : item.label);
        searchRequest.current++;
        locationRequest.current++;
        setSearchActive(false);
        setActiveSuggestion(-1);
        setLocating(false);
        setLocationError(null);
        selectedCode.current = item.code;
        select(item.code, item.level === "province" ? null : item.level === "tambon" ? item.code.slice(0, 6) : item.code, { code: item.code, level: item.level, name_th: "name_th" in item ? item.name_th : item.label, name_en: "", parents: "parents" in item ? item.parents : [] });
        setView("hazard");
        if (fromSearch)
            setHazard("overview");
        setSelectedDam(null);
        setSelectedRiver(null);
        setSelectedQuality(null);
        // Provinces and sub-districts get their own outline, and the map fits to it; districts use the map's selection.
        const request = ++outlineRequest.current;
        setOutline(null);
        if (item.level === "amphoe") {
            setFocus({ lat: item.lat, lon: item.lon, zoom: 9 });
        }
        else {
            api.boundary(item.code)
                .then((b) => request === outlineRequest.current && setOutline(b))
                .catch(() => request === outlineRequest.current && setFocus({ lat: item.lat, lon: item.lon, zoom: item.level === "province" ? 7 : 11 }));
        }
        setResults(null);
        setSearchStatus("idle");
        if (fromSearch)
            onAreaChosen?.();
    }
    function clearArea() {
        setAreaHistory([]);
        searchRequest.current++;
        locationRequest.current++;
        setQuery("");
        setResults(null);
        setSearchStatus("idle");
        setSearchActive(false);
        setActiveSuggestion(-1);
        setLocating(false);
        setLocationError(null);
        latestArea.current++;
        outlineRequest.current++;
        selectedCode.current = null;
        setArea(null);
        setPlace(null);
        setAreaStatus("idle");
        setSelected(null);
        setOutline(null);
        setSelectedDam(null);
        setSelectedRiver(null);
        setSelectedQuality(null);
        setFocus({ lat: 13.4, lon: 100.9, zoom: 5 });
    }
    function backArea() {
        const previous = areaHistory.at(-1);
        if (!previous) return;
        if (!previous.place) { clearArea(); return; }
        setAreaHistory(history => history.slice(0, -1));
        searchRequest.current++;
        locationRequest.current++;
        outlineRequest.current++;
        setResults(null);
        setSearchStatus("idle");
        setSearchActive(false);
        setActiveSuggestion(-1);
        setLocating(false);
        setLocationError(null);
        selectedCode.current = previous.place.code;
        select(previous.place.code, previous.selected, previous.place, false);
        setQuery(previous.place.name_th);
        setOutline(previous.outline);
        setFocus(previous.focus ? { ...previous.focus } : null);
        setSelectedDam(null);
        setSelectedRiver(null);
        setSelectedQuality(null);
        setView("hazard");
        if (!previous.outline) {
            const request = outlineRequest.current;
            api.boundary(previous.place.code).then(b => request === outlineRequest.current && setOutline(b)).catch(() => {});
        }
    }
    function showRiskLevel(level: RiskFilter | null) {
        clearArea();
        if (onRiskLevelChange)
            onRiskLevelChange(level);
        else {
            setLocalRiskLevel(level);
            setHazard("overview");
            setView("hazard");
        }
    }
    function chooseRiskArea(code: string) {
        selectFromMap(code);
        const point = riskAreas.find(a => a.code === code);
        if (point)
            setFocus({ lon: point.center[0], lat: point.center[1], zoom: 8 });
    }
    function chooseDam(dam: Dam) {
        setSelectedDam(damKey(dam));
        revealPanel();
        if (dam.lat != null && dam.lon != null)
            setFocus({ lat: dam.lat, lon: dam.lon, zoom: 8 });
    }
    // The detail card opens at the top of the side panel; bring it on screen when the list or map scrolled it away.
    const pointPanel = useRef<HTMLElement>(null);
    const revealPanel = useCallback(() => {
        requestAnimationFrame(() => {
            const el = pointPanel.current;
            if (!el)
                return;
            el.scrollTop = 0;
            const top = el.getBoundingClientRect().top;
            if (top < 0 || top > window.innerHeight - 160) {
                el.scrollIntoView({ block: "start", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
            }
        });
    }, []);
    const selectDamFromMap = useCallback((id: string) => { setSelectedDam(id); revealPanel(); }, [revealPanel]);
    // River stations of the selected area's province, for the callout on the area card.
    const areaRivers = area && area.rivers?.length ? {
        code: area.area.code.slice(0, 4),
        name: area.area.level === "province" ? area.area.name_th : area.area.parents[area.area.parents.length - 1] ?? area.area.name_th,
        count: area.rivers.length,
        over: area.rivers.filter((r) => r.status === "OVERFLOW").length,
    } : null;
    function openProvinceRivers() {
        if (!areaRivers)
            return;
        setView("rivers");
        setRiverSort(areaRivers.over ? "overflow" : "flow");
        setSelectedRiver(null);
        revealPanel();
        // Outline the province and fit the map to it, also when the card came from a tapped district.
        const request = ++outlineRequest.current;
        api.boundary(areaRivers.code).then((b) => request === outlineRequest.current && setOutline(b)).catch(() => { });
    }
    // The image panel mounts with the rain view; scroll to it after that render, not on a timer.
    const [imagesRequest, setImagesRequest] = useState(0);
    function showWeatherImages() {
        setView("hazard");
        setHazard("rain_storm");
        setImagesRequest((n) => n + 1);
    }
    useEffect(() => {
        if (!imagesRequest)
            return;
        const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        const images = document.getElementById("weather-images");
        const supplement = images?.closest("details");
        if (supplement)
            supplement.open = true;
        images?.scrollIntoView({ block: "nearest", behavior: reduce ? "auto" : "smooth" });
    }, [imagesRequest]);
    function chooseRiver(river: River) {
        setSelectedRiver(riverKey(river));
        setFocus({ lat: river.lat, lon: river.lon, zoom: 9 });
        revealPanel();
    }
    const selectRiverFromMap = useCallback((id: string) => { setSelectedRiver(id); revealPanel(); }, [revealPanel]);
    /** Open a dam or river station picked from search (or the Chao Phraya Dam marker) in its own view. */
    function openWater(item: WaterResult) {
        const province = item.kind === "dam" ? item.dam.province_code : item.river.province_code;
        // The lists follow the open area's province; another province's dam or station needs the whole country.
        if (provinceCode && province !== provinceCode) clearArea();
        else { setSearchActive(false); setResults(null); setSearchStatus("idle"); }
        if (item.kind === "dam") { setView("dams"); chooseDam(item.dam); }
        else { setView("rivers"); chooseRiver(item.river); }
        onAreaChosen?.();  // closes the header search dialog, as picking an area does
    }
    function openChaoPhrayaDam() {
        if (chaoPhrayaDam) openWater({ kind: "station", river: chaoPhrayaDam, key: CHAO_PHRAYA_DAM_ID, title: "", detail: "" });
    }
    const selectDamPoint = (id: string) => id === CHAO_PHRAYA_DAM_ID ? openChaoPhrayaDam() : selectDamFromMap(id);
    // Dams and river stations matching the search text, from the lists already loaded for the map.
    // Plain filter, not useMemo: empty unless a search is open, and the lists are small (35 dams, ~200 stations).
    const waterResults: WaterResult[] = (() => {
        const text = query.trim();
        if (!searchActive || text.length < 2) return [];
        const core = text.replace(/^(เขื่อน|อ่างเก็บน้ำ|อ่าง)\s*/, "").trim();
        const lowered = text.toLowerCase();
        const found: WaterResult[] = [];
        if (core.length >= 2) {
            for (const d of dams.list.filter((d) => d.name_th.includes(core) || text.includes(d.name_th)).slice(0, 5))
                found.push({ kind: "dam", dam: d, key: "dam:" + damKey(d), title: "เขื่อน" + d.name_th, detail: "เขื่อนขนาดใหญ่" + (d.province_th ? " · จ." + d.province_th : "") });
            const c13 = rivers.list.find((r) => r.code === CHAO_PHRAYA_DAM_STATION);
            if (c13 && "เจ้าพระยา".includes(core) && /เขื่อน|เจ้าพระยา/.test(text))
                found.push({ kind: "station", river: c13, key: CHAO_PHRAYA_DAM_ID, title: "เขื่อนเจ้าพระยา (เขื่อนทดน้ำ)", detail: "น้ำที่ปล่อยวัดที่สถานีท้ายเขื่อน C.13 · จ.ชัยนาท" });
        }
        const stations = rivers.list.filter((r) => r.code.toLowerCase() === lowered || r.code.toLowerCase().startsWith(lowered + ".")
            || (core.length >= 2 && ((r.name_th ?? "").includes(core) || (r.river_th ?? "").includes(core))));
        for (const r of stations.slice(0, 8))
            found.push({ kind: "station", river: r, key: "river:" + riverKey(r), title: `${r.code} ${r.name_th ?? ""}`.trim(), detail: [r.river_th, r.province_th && "จ." + r.province_th].filter(Boolean).join(" · ") });
        return found;
    })();
    // "เขื่อน…", "แม่น้ำ…" or a station code: the water results are what was asked for, so they go above the areas.
    const waterFirst = /^(เขื่อน|อ่าง|แม่น้ำ|[A-Za-z]{1,3}\.\d)/.test(query.trim());
    function chooseQuality(station: WaterQualityStation) {
        setSelectedQuality(qualityKey(station));
        if (station.lat != null && station.lon != null)
            setFocus({ lat: station.lat, lon: station.lon, zoom: 9 });
        revealPanel();
    }
    const selectQualityFromMap = useCallback((id: string) => { setSelectedQuality(id); revealPanel(); }, [revealPanel]);
    const runSearch = useCallback(async (text: string) => {
        const request = ++searchRequest.current;
        setSearchStatus("loading");
        setResults(null);
        try {
            const found = await api.search(text);
            if (request === searchRequest.current) {
                setResults(found);
                setSearchStatus("success");
                setActiveSuggestion(-1);
            }
        }
        catch {
            if (request === searchRequest.current)
                setSearchStatus("error");
        }
    }, []);
    useEffect(() => {
        if (!searchActive || query.trim().length < 2)
            return;
        searchTimer.current = setTimeout(() => { void runSearch(query.trim()); }, 300);
        return () => { if (searchTimer.current)
            clearTimeout(searchTimer.current); };
    }, [query, searchActive, runSearch]);
    useEffect(() => {
        const option = results?.[activeSuggestion];
        if (option)
            document.getElementById(`area-option-${option.code}`)?.scrollIntoView({ block: "nearest" });
    }, [activeSuggestion, results]);
    async function onSearch(event: FormEvent) {
        event.preventDefault();
        if (query.trim().length < 2)
            return;
        if (searchTimer.current)
            clearTimeout(searchTimer.current);
        setSearchActive(true);
        await runSearch(query.trim());
    }
    function locateMe() {
        if (!navigator.geolocation || !window.isSecureContext) {
            setLocationError("การใช้ตำแหน่งต้องเปิดเว็บผ่าน HTTPS หรือ localhost และใช้เบราว์เซอร์ที่รองรับ GPS");
            return;
        }
        const request = ++locationRequest.current;
        searchRequest.current++;
        setSearchActive(false);
        setResults(null);
        setSearchStatus("idle");
        setLocating(true);
        setLocationError(null);
        navigator.geolocation.getCurrentPosition(async (position) => {
            if (request !== locationRequest.current)
                return;
            try {
                const found = await api.locate(position.coords.latitude, position.coords.longitude);
                if (request === locationRequest.current)
                    chooseArea(found, true);
            }
            catch (error) {
                if (request === locationRequest.current) {
                    setLocating(false);
                    setLocationError(error instanceof Error && error.message.startsWith("404") ? "ยังไม่พบพื้นที่ในประเทศไทยจากตำแหน่งนี้ ลองค้นหาด้วยชื่อพื้นที่" : "ยังค้นหาพื้นที่จาก GPS ไม่ได้ กรุณาลองอีกครั้งหรือค้นหาด้วยชื่อพื้นที่");
                }
            }
        }, error => {
            if (request !== locationRequest.current)
                return;
            setLocating(false);
            setLocationError(error.code === 1 ? "กรุณาอนุญาตการเข้าถึงตำแหน่ง หรือค้นหาด้วยชื่อพื้นที่แทน" : error.code === 3 ? "ค้นหาตำแหน่งนานเกินไป กรุณาลองใหม่หรือค้นหาด้วยชื่อพื้นที่" : "ยังรับตำแหน่ง GPS ไม่ได้ ลองเปิดบริการตำแหน่งหรือค้นหาด้วยชื่อพื้นที่");
        }, { enableHighAccuracy: true, timeout: 12000, maximumAge: 60000 });
    }
    async function onAsk(event: FormEvent) {
        event.preventDefault();
        const asked = question.trim();
        if (!asked || asking)
            return;
        // Answering "which district?": send the earlier question with the reply, like a chat assistant would.
        const last = chat.at(-1);
        const sent = last?.answer?.intent === "clarify" ? `${last.sent} ${asked}` : asked;
        setQuestion("");
        setChat(old => [...old, { asked, sent, answer: null }]);
        const settle = (answer: AskAnswer) => setChat(old => old.map((turn, i) => i === old.length - 1 ? { ...turn, answer } : turn));
        try {
            // The area on screen (map pick or the last answer's area) answers "แล้วฝนตกไหม" without naming it again.
            const response = await api.ask(sent, area?.area.code ?? null);
            settle(response);
            if (response.area)
                chooseArea(response.area);
        }
        catch (err) {
            const text = err instanceof Error && err.message === "429"
                ? "ถามถี่เกินไป กรุณารอสักครู่แล้วถามใหม่ครับ"
                : "ยังตอบคำถามไม่ได้ในขณะนี้ กรุณาลองถามอีกครั้ง";
            settle({ text, intent: "error", area: null, sources: [] });
        }
        questionInput.current?.focus();
    }
    // From a shelter answer: the overview with the shelter layer on and its legend open at "หาศูนย์ใกล้ฉัน".
    // The visitor presses that button themself, so the browser asks for the location on their own tap.
    function openNearestShelters() {
        showSheltersOnMap();
        window.setTimeout(() => {
            const legend = document.querySelector<HTMLDetailsElement>("details.map-supplement");
            if (legend) legend.open = true;
            const find = document.querySelector<HTMLButtonElement>(".nearest-shelters .secondary-button");
            find?.scrollIntoView({ block: "center", behavior: "smooth" });
            find?.focus({ preventScroll: true });
        }, 300);
    }
    function answerView(a: AskAnswer) {
        // The answer text already ends with its "ที่มา:" line; only add the list when it does not.
        const showSources = a.sources.length > 0 && !a.text.includes("ที่มา:");
        return <div className={"answer-text " + (a.intent === "error" ? "data-notice" : "")}><AnswerText text={a.text}/>
            {showSources && <p className="source-note">{tr("ที่มา: ")}{tr(a.sources.join(", "))}</p>}
            {a.intent === "shelter" && <button type="button" className="secondary-button" onClick={openNearestShelters}><Icon name="shelter"/>{tr("หาศูนย์ใกล้ฉันและดูเส้นทาง")}</button>}
            {a.llm_used && <p className="source-note">{tr("เรียบเรียงโดย AI")}{a.llm_model ? ` (${a.llm_model})` : ""}{" · "}{tr(a.intent === "greeting" || a.intent === "out_of_scope" ? "คำตอบทั่วไป ไม่ใช่ข้อมูลภัย" : "ตัวเลขและระดับเตือนภัยมาจากข้อมูลและกฎของระบบ ไม่ได้ให้ AI ตัดสิน")}</p>}
        </div>;
    }
    const counts = LEVELS.map((level) => ({ ...level, n: displayedLayer?.features.filter((f) => (f.properties?.level ?? "NONE") === level.id).length ?? 0 }));
    const summaryCounts = LEVELS.map(level => ({ ...level, n: summaryLayer?.features.filter(f => (f.properties?.level ?? "NONE") === level.id).length ?? 0 }));
    const warningCount = counts.filter((l) => l.id === "CRITICAL" || l.id === "WARNING").reduce((sum, l) => sum + l.n, 0);
    const assessments = (area?.assessments ?? []).filter(a => hazard === "overview" || a.hazard === hazard)
        .sort((a, b) => LEVELS.findIndex(l => l.id === (a.level ?? "NONE")) - LEVELS.findIndex(l => l.id === (b.level ?? "NONE")));
    const locationScope = place && <div className="location-scope"><Icon name="pin"/><div><strong>{tr(prefix(place.level))}{tr(place.name_th)}</strong><small>{tr("ข้อมูล")}{tr(itemTh(currentItem))}{tr("ระดับจังหวัด \u00B7 จ.")}{tr(areaProvince?.name)}</small></div><button type="button" onClick={clearArea}>{tr("ดูทั่วประเทศ")}</button></div>;
    // Overview: how many districts each hazard puts at WATCH or above (as its worst hazard).
    const overviewBreakdown = useMemo(() => {
        if (hazard !== "overview" || !layer)
            return [];
        const n = new Map<MapLayerId, number>();
        for (const f of layer.features) {
            const h = f.properties?.worst_hazard as MapLayerId | null;
            if (h)
                n.set(h, (n.get(h) ?? 0) + 1);
        }
        return [...n.entries()].sort((a, b) => b[1] - a[1]) as [
            Exclude<MapLayerId, "overview">,
            number
        ][];
    }, [hazard, layer]);
    // One line for the page header: how many districts are at WARNING or CRITICAL over all hazards, which hazards
    // cause most of them and where. Counted from the same overview layer as the level chips (rules.yaml levels only).
    const headline = useMemo(() => {
        if (!summaryLayer)
            return null;
        const risky = summaryLayer.features.filter(f => f.properties?.level === "CRITICAL" || f.properties?.level === "WARNING");
        const ranked = (key: "worst_hazard" | "province_th") => {
            const n = new Map<string, number>();
            for (const f of risky) {
                const value = f.properties?.[key];
                if (typeof value === "string" && value)
                    n.set(value, (n.get(value) ?? 0) + 1);
            }
            return [...n.entries()].sort((a, b) => b[1] - a[1]).map(([value]) => value);
        };
        return { total: risky.length, critical: risky.filter(f => f.properties?.level === "CRITICAL").length,
                 hazards: ranked("worst_hazard").slice(0, 2) as MapLayerId[], provinces: ranked("province_th").slice(0, 3) };
    }, [summaryLayer]);
    const headlineView = headline
        ? <button type="button" className={"national-headline" + (headline.critical ? " is-critical" : headline.total ? " is-warning" : " is-calm")} disabled={!headline.total} title={headline.total ? tr(number.format(headline.total) + " อำเภอต้องระวัง · ส่วนใหญ่จาก" + headline.hazards.map(h => itemTh(h)).join(" และ ") + " · " + headline.provinces.map(p => "จ." + p).join(" ")) : undefined} onClick={() => showRiskLevel(headline.critical ? "CRITICAL" : "WARNING")}>
            <span className="headline-dot" aria-hidden="true"/>
            {headline.total
                ? <span><strong>{tr(number.format(headline.total))}{tr(" อำเภอต้องระวัง")}</strong>{headline.critical > 0 && <b>{tr(" · วิกฤต ")}{tr(number.format(headline.critical))}</b>}<small>{tr(" · ส่วนใหญ่จาก")}{tr(headline.hazards.map(h => itemTh(h)).join(tr(" และ ")))}{headline.provinces.length > 0 && <>{tr(" · ")}{tr(headline.provinces.map(p => tr("จ.") + tr(p)).join(" "))}</>}</small></span>
                : <span><strong>{tr("ไม่มีอำเภอในระดับเตือนภัยหรือวิกฤต")}</strong><small>{tr(" · ข้อมูลล่าสุดทั่วประเทศ")}</small></span>}
          </button>
        : <p className="national-headline is-loading">{tr("กำลังรวมสถานการณ์ทั่วประเทศ…")}</p>;
    const waterBlock = searchActive &&!!waterResults.length && <WaterResults items={waterResults} onOpen={openWater}/>;
    const searchPanel = (<section className="search-panel">
            <h2>{tr("พื้นที่ที่คุณอยากรู้")}</h2><p className="section-description" id="area-search-description">{tr("พิมพ์ชื่อจังหวัด อำเภอ หรือตำบลที่คุณสนใจ เพื่อดูความเสี่ยงและสถานการณ์ภัยในพื้นที่")}</p>
            <form onSubmit={onSearch} className="search-form" role="search">
              <label className="sr-only" htmlFor="area-search">{tr("ค้นหาพื้นที่")}</label>
              <div className="input-wrap"><Icon name="search"/><input id="area-search" role="combobox" aria-autocomplete="list" aria-expanded={!!(searchActive && results?.length)} aria-controls="area-suggestions" aria-activedescendant={activeSuggestion >= 0 && results?.[activeSuggestion] ? `area-option-${results[activeSuggestion].code}` : undefined} aria-describedby="area-search-description" ref={searchInput} placeholder={tr("พิมพ์ชื่อพื้นที่อย่างน้อย 2 ตัวอักษร")} autoComplete="off" value={query} maxLength={100} onFocus={() => setSearchActive(true)} onChange={(event) => { setQuery(event.target.value); searchRequest.current++; locationRequest.current++; setLocating(false); setLocationError(null); setSearchActive(true); setActiveSuggestion(-1); setResults(null); setSearchStatus("idle"); }} onKeyDown={(event) => {
            if (event.nativeEvent.isComposing)
                return;
            if (event.key === "Escape" && searchActive) {
                event.preventDefault();
                event.stopPropagation();
                searchRequest.current++;
                setSearchActive(false);
                setResults(null);
                setSearchStatus("idle");
            }
            if (!searchActive || !results?.length)
                return;
            if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                setActiveSuggestion(index => event.key === "ArrowDown" ? (index + 1) % results.length : (index <= 0 ? results.length - 1 : index - 1));
            }
            if (event.key === "Enter" && activeSuggestion >= 0) {
                event.preventDefault();
                chooseArea(results[activeSuggestion], true);
            }
        }}/></div>
              <button className="primary-button" disabled={query.trim().length < 2 || searchStatus === "loading"}>{tr(searchStatus === "loading" ? <span className="spinner"/> : "ค้นหา")}</button>
            </form>
            <button type="button" className="location-button" onClick={locateMe} disabled={locating}>{tr(locating ? <span className="spinner"/> : <Icon name="pin"/>)}{tr(locating ? "กำลังหาตำแหน่ง…" : "ตำแหน่งของฉัน")}</button>
            {tr(locationError && <p role="alert" className="data-notice location-error">{tr(locationError)}</p>)}
            <div aria-live="polite">
              {tr(searchStatus === "loading" && <p className="search-feedback">{tr("กำลังค้นหาพื้นที่\u2026")}</p>)}
              {tr(searchStatus === "error" && <p className="search-feedback data-notice">{tr("ค้นหาไม่สำเร็จ กรุณากดค้นหาเพื่อลองอีกครั้ง")}</p>)}
              {tr(results?.length === 0 && !waterResults.length && <p className="search-feedback">{tr("ไม่พบพื้นที่นี้ ลองใช้ชื่อจังหวัด อำเภอ เขื่อน หรือแม่น้ำ")}</p>)}
            </div>
            {tr(waterFirst && waterBlock)}
            {tr(searchActive && !!results?.length && <div className="search-results"><div className="results-heading"><span>{tr("พื้นที่แนะนำ \u00B7 ")}{tr(results.length)}{tr(" รายการ")}</span><button className="icon-button" aria-label={tr("ปิดผลการค้นหา")} onClick={() => { searchRequest.current++; setSearchActive(false); setResults(null); setSearchStatus("idle"); }}><Icon name="close"/></button></div><ul id="area-suggestions" role="listbox" aria-label={tr("พื้นที่แนะนำ")}>{tr(results.map((item, index) => <li key={item.code} role="presentation"><button role="option" id={`area-option-${item.code}`} aria-selected={activeSuggestion === index} tabIndex={-1} onClick={() => chooseArea(item, true)}><Icon name="pin"/><span><strong>{tr(prefix(item.level))}{tr(item.name_th)}</strong><small>{tr(item.parents.join(" · ") || "ประเทศไทย")}</small></span><Icon name="arrow"/></button></li>))}</ul></div>)}
            {tr(!waterFirst && waterBlock)}
          </section>);
    const subTabs = (<>
          {tr(currentGroup.items.length > 1 && <div className="hazard-subtabs" role="group" aria-label={tr("เลือกภัยในกลุ่ม" + currentGroup.th)}>
            {tr(currentGroup.items.map((item) => { const active = item === currentItem; return <button key={item} type="button" data-subcategory={item} className={active ? "is-active" : ""} aria-pressed={active} onClick={() => openItem(item)}><Icon name={item === "overview" ? "shield" : item === "dams" ? "dam" : item === "rivers" || item === "water-quality" ? "river" : item}/>{tr(itemTh(item))}</button>; }))}
          </div>)}
  </>);
    const overviewControls = (<>
        {tr(overview && <aside className="layer-toolbar" aria-label={tr("ชั้นข้อมูลบนแผนที่")}>
          <div className="layer-toolbar-buttons" role="group" aria-label={tr("เปิดปิดชั้นข้อมูลภาพรวม")}>
            <button type="button" data-layer="pm25" aria-pressed={pmOn} onClick={() => setPmOn(on => !on)}><Icon name="air_quality"/><span>{tr("ฝุ่น PM2.5")}<small>{tr(!pmOn ? "ปิด" : airStatus === "loading" ? "กำลังโหลด…" : airStatus === "error" ? "โหลดไม่สำเร็จ" : "เปิด")}</small></span><span className="layer-toggle" aria-hidden="true"/></button>
            <button type="button" data-layer="shelters" aria-pressed={shelterOn} onClick={toggleShelters}><Icon name="shelter"/><span>{tr("ศูนย์พักพิง")}<small>{tr(!shelterOn ? "ปิด" : shelters.status === "loading" ? "กำลังโหลด…" : shelters.status === "error" ? "โหลดไม่สำเร็จ" : "เปิด")}</small></span><span className="layer-toggle" aria-hidden="true"/></button>
            {radarAvailable && <button type="button" data-layer="radar" aria-pressed={radarOn} onClick={() => setRadarOn(on => !on)}><Icon name="radar"/><span>{tr("เรดาร์ฝน")}<small>{tr(radarOn ? "เปิด" : "ปิด")}</small></span><span className="layer-toggle" aria-hidden="true"/></button>}
            <OverlayControls data={overlays.data} enabled={overlays.enabled} onToggle={overlays.toggle}/>
          </div>
        </aside>)}
    </>);
    const mapControls = (<div className="map-controls">
          {tr(!hideToolbar && <div className="hazard-toolbar" role="group" aria-label={tr("เลือกกลุ่มภัย")}>
            {tr(GROUPS.map((group) => { const active = group.id === currentGroup.id; return <button key={group.id} type="button" className={"hazard-button " + (active ? "is-active" : "")} aria-pressed={active} onClick={() => openItem(lastInGroup.current[group.id] ?? group.items[0])}><Icon name={group.icon}/><span>{tr(group.th)}</span></button>; }))}
          </div>)}
          {tr(!hideToolbar && subTabs)}
          </div>);
    return (<>
      {tr(searchInHeader && searchTarget && createPortal(searchPanel, searchTarget))}
      {tr(locationTarget && createPortal(<>{areaHistory.length > 0 && <button type="button" className="clear-location-button back-location-button" aria-label={tr("ย้อนกลับพื้นที่ก่อนหน้า")} title={tr("ย้อนกลับพื้นที่ก่อนหน้า")} onClick={backArea}><Icon name="arrow"/></button>}<button className={"journey-search-icon" + (place ? " has-location" : "")} type="button" aria-label={tr(place ? tr("เปลี่ยนพื้นที่ ") + tr(prefix(place.level)) + tr("") + tr(place.name_th) + tr("") : undefined)} title={tr(place ? [place.name_th, ...place.parents].join(" · ") : undefined)} onClick={onOpenSearch}><Icon name={place ? "pin" : "search"}/><span>{tr(place ? `${prefix(place.level)}${place.name_th}${place.parents.length ? ` · ${place.parents.at(-1)}` : ""}` : "พื้นที่ที่คุณอยากรู้")}</span></button>{tr(place && <button type="button" className="clear-location-button" aria-label={tr("ล้างพื้นที่ที่ค้นหา")} title={tr("ล้างพื้นที่ที่ค้นหา")} onClick={() => { clearArea(); requestAnimationFrame(() => locationTarget.querySelector("button")?.focus({ preventScroll: true })); }}><Icon name="close"/></button>)}</>, locationTarget))}
      {headlineTarget && createPortal(headlineView, headlineTarget)}
      {tr(summaryTarget ? createPortal(<RiskSnapshot total={summaryLayer ? summaryLayer.features.length : null} counts={summaryCounts} scope="ภาพรวมทุกภัย" activeLevel={selectedRiskLevel} overviewActive={overview} onSelect={showRiskLevel}/>, summaryTarget) : !searchInHeader && <RiskSnapshot total={summaryLayer ? summaryLayer.features.length : null} counts={summaryCounts} scope="ภาพรวมทุกภัย" activeLevel={selectedRiskLevel} overviewActive={overview} onSelect={showRiskLevel}/>)}
      <div className="dashboard-grid">
        {tr(!searchInHeader && <a className="mobile-search-link" href="#area-search"><Icon name="search"/>{tr("ค้นหาจังหวัด อำเภอ หรือตำบล")}<Icon name="arrow"/></a>)}
        <section className="map-panel" aria-label={tr("ภาพรวมความเสี่ยง")}>
          <div className="map-chrome">{tr(!hideToolbar && mapControls)}<div className={"map-heading" + (overview ? " has-overview-controls" : "")}>
            {overview && <div className="overview-heading-controls">{overviewControls}</div>}
            <div className="map-heading-copy">
              <h2>{tr(selectedRiskLevel ? tr("พื้นที่ระดับ") + tr(levelInfo(selectedRiskLevel).th) + tr("ทั่วประเทศ") : view === "water-quality" ? "แผนที่สถานีวัดคุณภาพน้ำ" : view === "dams" ? "แผนที่ปริมาณน้ำในเขื่อน" : view === "rivers" ? "แผนที่ระดับน้ำในแม่น้ำ" : tr("แผนที่ความเสี่ยง") + tr(itemTh(hazard)) + tr(""))}</h2>
              <p>{tr(view === "water-quality" ? "ความเค็ม ออกซิเจนละลายน้ำ และค่าคุณภาพน้ำรายสถานี" : view === "dams" ? "ปริมาณน้ำและการระบายน้ำของเขื่อนขนาดใหญ่ทั่วประเทศ" : view === "rivers" ? "ระดับน้ำเทียบตลิ่งและปริมาณน้ำไหลผ่านจากสถานีวัดน้ำ" : hazardDescriptions[hazard])}</p>
            </div>
            {view === "hazard" && (hazard === "overview" || areaStatus !== "idle") && <EnvironmentSummary layers={environmentLayers} area={area} loadingArea={areaStatus === "loading"} areaError={areaStatus === "error"}/>}
            <div className="map-heading-actions">
            {tr(view === "hazard" && radarAvailable && !overview && RADAR_LAYERS.includes(hazard) && <button type="button" title={tr("เรดาร์บนแผนที่")} className={"images-link " + (radarOn ? "is-on" : "")} aria-pressed={radarOn} onClick={() => setRadarOn((on) => !on)}><Icon name="radar"/>{tr("เรดาร์บนแผนที่")}{tr(radarOn ? " · เปิด" : " · ปิด")}</button>)}
            {tr(view === "hazard" && hazard === "rain_storm" && <button type="button" className="images-link" title={tr("ภาพเรดาร์ คาดการณ์ฝน และพายุ")} onClick={showWeatherImages}><Icon name="radar"/>{tr("ภาพเรดาร์ \u00B7 คาดการณ์ฝน \u00B7 พายุ")}</button>)}
            <button className="icon-button" onClick={() => setRevision((value) => value + 1)} disabled={view === "water-quality" ? quality.status === "loading" : layerLoading} aria-label={tr("โหลดข้อมูลแผนที่ใหม่")} title={tr("โหลดข้อมูลแผนที่ใหม่")}><Icon name="refresh" className={layerLoading ? "spinning" : ""}/></button>
            </div>
          </div>
          </div><div className="map-canvas">
            <HazardMap data={displayedLayer} selected={selected} onSelect={selectFromMap} focus={focus} outline={outline} mode={selectedRiskLevel ? "risk" : view === "hazard" ? "hazard" : "points"} points={selectedRiskLevel ? riskPoints : view === "water-quality" ? qualityPoints : view === "dams" ? damPoints : view === "rivers" ? riverPoints : null} selectedPoint={selectedRiskLevel ? selected : view === "water-quality" ? selectedQuality : view === "dams" ? selectedDam : selectedRiver} onSelectPoint={selectedRiskLevel ? chooseRiskArea : view === "water-quality" ? selectQualityFromMap : view === "dams" ? selectDamPoint : selectRiverFromMap} label={tr(selectedRiskLevel ? tr("แผนที่พื้นที่ระดับ") + tr(levelInfo(selectedRiskLevel).th) + tr("") : view === "water-quality" ? "แผนที่สถานีวัดคุณภาพน้ำ" : view === "dams" ? "แผนที่เขื่อนขนาดใหญ่" : view === "rivers" ? "แผนที่สถานีวัดน้ำในแม่น้ำ" : undefined)} stations={view === "hazard" && !selectedRiskLevel ? stationLayer : null} shelters={overview && !selectedRiskLevel && shelterOn ? shelterLayer : null} route={overview && !selectedRiskLevel && shelterOn ? routeLine : null} radar={showRadar && !selectedRiskLevel ? radarFrame?.tiles ?? null : null} radarNext={showRadar && !selectedRiskLevel && radarPreload !== radarFrame?.tiles ? radarPreload : null} onRadarImage={radarImage} radarDimmed={radarFrame?.kind === "forecast"} radarShift={radarFrame?.shift ?? null} radarForecast={showRadar && !selectedRiskLevel ? radarForecast : null} overlays={overview && !selectedRiskLevel ? { flood: overlays.enabled.flood ? overlays.data.flood : null, cyclones: overlays.enabled.cyclones ? overlays.data.cyclones : null, waves: overlays.enabled.waves ? overlays.data.waves : null } : undefined} dark={darkMap} riskOnly={view === "hazard" && riskOnly}/>
            <div className="map-options" role="group" aria-label={tr("ตัวเลือกแผนที่")}>
              <button type="button" className="map-reset" onClick={clearArea}><Icon name="map"/>{tr("ภาพรวมประเทศ")}</button>
              <button type="button" aria-pressed={darkMap} className={darkMap ? "is-active" : ""} onClick={() => setDarkMap(!darkMap)}>{tr("แผนที่กลางคืน")}</button>
              {tr(view === "hazard" && <button type="button" aria-pressed={riskOnly} className={riskOnly ? "is-active" : ""} onClick={() => setRiskOnly(!riskOnly)}>{tr("เฉพาะที่เสี่ยง")}</button>)}
            </div>
            <div className="map-hint"><Icon name="pin"/><span>{tr(selectedRiskLevel ? "จุดแสดงตำแหน่งอำเภอ · เลือกเพื่ออ่านรายละเอียด" : view === "water-quality" ? "แตะวงกลมเพื่อดูค่าคุณภาพน้ำ" : view === "dams" ? "แตะวงกลมเพื่อดูข้อมูลเขื่อน" : view === "rivers" ? "แตะวงกลมเพื่อดูข้อมูลสถานีวัดน้ำ" : "แตะพื้นที่เพื่อดูรายละเอียด")}</span></div>
            {tr(view === "hazard" && <>
              {tr(layerLoading && <div className="map-notice" role="status"><span className="spinner"/>{tr("กำลังโหลดข้อมูลความเสี่ยง\u2026")}</div>)}
              {tr(layerError && <div className="map-notice error-notice" role="alert"><Icon name="info"/><div><strong>{tr("โหลดข้อมูลความเสี่ยงไม่สำเร็จ")}</strong><p>{tr("ตรวจสอบการเชื่อมต่อ แล้วลองอีกครั้ง")}</p></div><button className="text-button" onClick={() => setRevision((value) => value + 1)}>{tr("ลองใหม่")}</button></div>)}
              {tr(layer && layer.features.length === 0 && <div className="map-notice" role="status">{tr("ยังไม่มีข้อมูลความเสี่ยงสำหรับภัยประเภทนี้")}</div>)}
            </>)}
            {tr(view === "dams" && dams.status === "loading" && <div className="map-notice" role="status"><span className="spinner"/>{tr("กำลังโหลดข้อมูลเขื่อน\u2026")}</div>)}
            {tr(view === "dams" && dams.status === "error" && !dams.list.length && <div className="map-notice error-notice" role="alert"><Icon name="info"/><div><strong>{tr("โหลดข้อมูลเขื่อนไม่สำเร็จ")}</strong><p>{tr("ตรวจสอบการเชื่อมต่อ แล้วลองอีกครั้ง")}</p></div><button className="text-button" onClick={() => setRevision((value) => value + 1)}>{tr("ลองใหม่")}</button></div>)}
            {tr(view === "rivers" && rivers.status === "loading" && <div className="map-notice" role="status"><span className="spinner"/>{tr("กำลังโหลดข้อมูลแม่น้ำ\u2026")}</div>)}
            {tr(view === "rivers" && rivers.status === "error" && !rivers.list.length && <div className="map-notice error-notice" role="alert"><Icon name="info"/><div><strong>{tr("โหลดข้อมูลแม่น้ำไม่สำเร็จ")}</strong><p>{tr("ตรวจสอบการเชื่อมต่อ แล้วลองอีกครั้ง")}</p></div><button className="text-button" onClick={() => setRevision((value) => value + 1)}>{tr("ลองใหม่")}</button></div>)}
          </div>
          {showRadar && !selectedRiskLevel && radarAvailable && radarFrames.length > 0 && <RadarPlayback frames={radarFrames} index={radarFrameIndex} explanation={radarNowcast?.explanation ?? ""} explanationSource={radarNowcast?.explanation_source ?? "template"} explanationPending={!!radarNowcast?.explanation_pending} onChange={(index) => { const frame = radarFrames[index]; setRadarSelection(frame ? { time: frame.time, kind: frame.kind } : null); }} onPreload={setRadarPreload}/>}
          {/* The overview shows the legend only while a layer is on; it explains the colours each layer draws. */}
          {(!overview || overviewLegend) && <details className="map-supplement"><summary>{tr(overview ? "ชั้นข้อมูลและคำอธิบายแผนที่" : "คำอธิบายและข้อมูลเพิ่มเติม")}</summary><div className="map-supplement-body">
          {tr(overviewLegend && <div className="overview-overlay-summary">
            <OverlayLegends data={overlays.data} enabled={overlays.enabled}/>
            {tr(pmOn && <section><h4>{tr("ฝุ่น PM2.5 · เฉลี่ย 24 ชม.")}</h4><ul className="overlay-levels">{tr(PM25_BANDS.map(b => <li key={b.th}><span className="legend-dot" style={{ background: b.color }}/>{tr(b.th)}</li>))}</ul><p>{tr(airStatus === "loading" ? "กำลังโหลด…" : airStatus === "error" ? "โหลดไม่สำเร็จ · แสดงข้อมูลเดิมถ้ามี" : !airStations?.length ? "ยังไม่มีข้อมูลสถานี" : tr("") + tr(airStations.length) + tr(" สถานี · แต่ละสถานีอาจรายงานคนละเวลา"))}</p><small>{tr("Air4Thai (กรมควบคุมมลพิษ) · เวลาล่าสุด ")}{tr(formatTime(airStations?.map(s => s.time).sort().at(-1)))}{tr(" (เวลาไทย)")}</small></section>)}
            {tr(shelterOn && <section><h4>{tr("ศูนย์พักพิงชั่วคราว")}</h4><ul className="overlay-levels"><li><span className="legend-dot" style={{ background: SHELTER_COLOR, borderRadius: "50%" }}/>{tr("ตำแหน่งศูนย์")}</li><li><span className="legend-dot" style={{ background: "#ffffff", border: `2px solid ${SHELTER_COLOR}`, borderRadius: "50%" }}/>{tr("ตำแหน่งโดยประมาณ")}</li></ul><p>{tr(shelters.status === "loading" ? "กำลังโหลด…" : shelters.status === "error" ? "โหลดไม่สำเร็จ" : tr("") + tr(number.format(shelters.list.length)) + tr(" แห่ง · ยังไม่มีข้อมูลกรุงเทพฯ · กดจุดเพื่อดูความจุ"))}</p><p>{tr("ตัวเลขในวง = จำนวนศูนย์ในบริเวณนั้น กดวงเพื่อซูมเข้า")}</p><NearestShelters onRoute={setShelterRoute}/><p>{tr("ก่อนเดินทาง โปรดสอบถาม อปท. ในพื้นที่ หรือโทรสายด่วน ปภ. 1784 ว่าศูนย์เปิดอยู่")}</p><small>{tr("กรมป้องกันและบรรเทาสาธารณภัย (data.go.th) · ปรับปรุงล่าสุด ")}{tr(shelters.updated ? new Date(shelters.updated).toLocaleDateString(getLocale(), { day: "numeric", month: "short", year: "numeric" }) : "—")}</small></section>)}
          </div>)}
          {tr(view === "water-quality" ? <div className="map-summary"><div className="legend-heading"><h3>{tr("สถานีวัดคุณภาพน้ำ")}</h3><span>{tr(visibleQuality.length)}{tr(" สถานี")}</span></div><p className="source-note"><span style={{ color: "#e07b16" }}>●</span>{tr(" มีค่าเกินเกณฑ์ที่ตรวจ \u00B7 ")}<span style={{ color: "#2f7fb5" }}>●</span>{tr(" ไม่พบค่าเกินเกณฑ์ที่ตรวจ \u00B7 ")}<span style={{ color: "#8a97a3" }}>●</span>{tr(" ไม่มีค่าความเค็มและออกซิเจน")}</p><div className="data-timestamp"><Icon name="clock"/><span>{tr("ข้อมูล ณ ")}{tr(formatTime(quality.updated))}{tr(" (เวลาไทย)")}</span></div></div> : view === "rivers" ? <div className="map-summary">
            <div className="legend-heading"><h3>{tr("สถานะน้ำเทียบตลิ่ง")}{tr(riverProvince && tr(" \u00B7 จ.") + tr(riverProvince.name) + tr(""))}</h3><span>{tr(rivers.list.length ? number.format(visibleRivers.length) + " สถานี" : "รอข้อมูล")}</span></div>
            <ul className="risk-legend">{tr(RIVER_STATUS.map((c) => <li key={c.id}><span className="legend-dot" style={{ background: c.color }}/><span>{tr(c.th)}</span><strong>{tr(rivers.list.length ? number.format(visibleRivers.filter((r) => r.status === c.id).length) : "—")}</strong></li>))}</ul>
            {tr(belowChaoPhrayaDam?.discharge_m3s != null && <p className="dam-total-release">{tr("เขื่อนเจ้าพระยา (ชัยนาท) ปล่อยน้ำลงท้ายเขื่อน ")}<strong>{tr(number.format(Math.round(belowChaoPhrayaDam.discharge_m3s)))}{tr(" ลบ.ม./วินาที")}</strong>{tr(" (สถานี C.13, ")}{tr(riverTime(belowChaoPhrayaDam.time))})</p>)}
            <div className="data-timestamp"><Icon name="clock"/><span>{tr("ข้อมูลรายชั่วโมงจาก ThaiWater (สสน.) \u00B7 ")}<a href={RID_HOURLY} target="_blank" rel="noreferrer noopener">{tr("รายงานน้ำรายชั่วโมงของกรมชลประทาน")}</a></span></div>
          </div> : view === "dams" ? <div className="map-summary">
            <div className="legend-heading"><h3>{tr("สถานะน้ำในเขื่อน")}</h3><span>{tr(dams.status === "success" || visibleDams.length ? number.format(visibleDams.length) + " เขื่อน" : "รอข้อมูล")}</span></div>
            <ul className="risk-legend">{tr(DAM_LEGEND.map((c) => <li key={c.th}><span className="legend-dot" style={{ background: c.color }}/><span>{tr(c.th)}</span><strong>{tr(dams.status === "success" || visibleDams.length ? number.format(visibleDams.filter(c.match).length) : "—")}</strong></li>))}</ul>
            {tr(!!visibleDams.length && <p className="dam-total-release">{tr("เขื่อนในแผนที่รวมกันระบายน้ำ ")}<strong>{tr(mcm(totalRelease))}{tr(" ล้าน ลบ.ม.")}</strong>{tr(" (เฉลี่ยราว ")}{tr(m3s(totalRelease))}{tr(" ลบ.ม./วินาที)")}</p>)}
            <div className="data-timestamp"><Icon name="clock"/><span>{tr("ข้อมูลรายวันของวันที่ ")}{tr(damDate(visibleDams[0]?.date ?? null))}{tr(" \u00B7 % เทียบกับระดับกักเก็บปกติ")}</span></div>
          </div> : overview ? null : <div className="map-summary">
            <div className="legend-heading"><h3>{tr("ระดับความเสี่ยง")}</h3><span>{tr(displayedLayer ? number.format(displayedLayer.features.length) + " อำเภอ" : "รอข้อมูล")}</span></div>
            <ul className="risk-legend">{tr(counts.map((level) => <li key={level.id}><span className="legend-dot" style={{ background: level.color }}/><span>{tr(level.th)}</span><strong>{tr(layer ? number.format(level.n) : "—")}</strong></li>))}</ul>
            {tr(hazard === "air_quality" && <div className="pm-legend"><h3>{tr("จุดสถานีวัด PM2.5 (กรมควบคุมมลพิษ) \u00B7 ตัวเลขคือค่าเฉลี่ย 24 ชม. \u00B7 วงใหญ่ = หลายสถานี แสดงค่าสูงสุด")}</h3><ul>{tr(PM25_BANDS.map((b, i) => <li key={b.th}><span className="legend-dot" style={{ background: b.color }}/>{tr(b.th)}<small>{tr(i === 0 ? "0–15" : i === PM25_BANDS.length - 1 ? "> 75" : `${PM25_BANDS[i - 1].max}–${b.max}`)}</small></li>))}</ul>{tr(airStations && <p className="source-note">{tr(number.format(airStations.length))}{tr(" สถานีที่มีค่าล่าสุดไม่เกิน 6 ชม. \u00B7 อำเภอที่ไม่มีสถานีใช้ค่าจากแบบจำลอง")}</p>)}</div>)}
            <div className="data-timestamp"><Icon name="clock"/><span>{tr("ข้อมูล ณ ")}{tr(formatTime(layer?.meta.data_updated_at))}{tr(" (เวลาไทย)")}{tr(pending && " · กำลังโหลดข้อมูลดาวเทียมและพยากรณ์เพิ่มเติม")}</span></div>
            {tr(!!layer?.meta.degraded_services.length && <p className="data-notice">{tr("ข้อมูลบางแหล่งไม่พร้อมใช้งาน: ")}{tr(layer.meta.degraded_services.join(", "))}</p>)}
          </div>)}
          {tr(view === "hazard" && hazard === "rain_storm" && <div id="weather-images" className="media-anchor"><MediaPanel revision={revision}/></div>)}
          {view === "hazard" && hazard === "tropical_cyclone" && <StormsPanel revision={revision}/>}
          {tr(view === "hazard" && (hazard === "earthquake" || hazard === "tropical_cyclone") && <EventsPanel revision={revision} quakes={hazard === "earthquake"}/>)}
          </div></details>}
            {tr(showRadar && radarAvailable && radarFrame?.kind !== "forecast" && <p className="source-note radar-note">{tr(!radarFrame ? "ยังโหลดเรดาร์ไม่ได้" : <>{tr("เรดาร์ฝนเวลา ")}{tr(new Date(radarFrame.time * 1000).toLocaleTimeString(getLocale(), { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Bangkok" }))}{tr(" น. · ")}<a href="https://www.rainviewer.com/" target="_blank" rel="noreferrer noopener">RainViewer</a>{tr(" (ภาพรวมจากเรดาร์หลายแหล่ง บางพื้นที่อาจไม่ครอบคลุม)")}</>)}</p>)}
        </section>

        <aside className="detail-sidebar" aria-label={tr("ค้นหาและดูรายละเอียดพื้นที่")}>
          {tr(!searchInHeader && searchPanel)}

          {tr(selectedRiskLevel && <RiskAlerts level={selectedRiskLevel} areas={riskAreas} loading={layerLoading} error={layerError} updated={formatTime(overviewData?.meta.data_updated_at)} selected={selected} area={area} areaStatus={areaStatus} onSelect={chooseRiskArea} onClear={() => showRiskLevel(null)} onRetry={() => setRevision(n => n + 1)}/>)}
          <section className="area-panel" aria-label={tr("รายละเอียดพื้นที่")} aria-busy={areaStatus === "loading"} hidden={view !== "hazard" || !!selectedRiskLevel}>
            {tr(areaStatus === "loading" ? <div className="empty-area" role="status"><span className="spinner"/><h3>{tr("กำลังดูข้อมูลพื้นที่\u2026")}</h3><p>{tr("รวบรวมระดับความเสี่ยงและข้อมูลล่าสุด")}</p></div> : areaStatus === "error" ? <div className="empty-area" role="alert"><Icon name="info"/><h3>{tr("ยังเปิดข้อมูลพื้นที่ไม่ได้")}</h3><p>{tr("กรุณาลองใหม่ หรือเลือกพื้นที่อื่น")}</p><button className="secondary-button" onClick={() => selectedCode.current && select(selectedCode.current, selected)}>{tr("ลองใหม่")}</button></div> : area ? <>
              <div className="area-title"><span className="area-icon"><Icon name="pin"/></span><div><h2>{tr(prefix(area.area.level))}{tr(area.area.name_th)}</h2><p>{tr(area.area.parents.join(" · ") || "ประเทศไทย")}</p></div><button type="button" className="text-button area-reset" onClick={clearArea}>{tr("ดูทั่วประเทศ")}</button></div>
              {tr(area.area.level === "province" && <p className="data-notice">{tr("ระดับจังหวัดแสดงความเสี่ยงสูงสุดของอำเภอในจังหวัด")}</p>)}
              <AreaWarnings warnings={area.official_warnings} code={area.area.code} hazard={hazard} assessments={assessments}/>
              <ul className="assessment-list">{tr(assessments.map((assessment) => {
                const info = levelInfo(assessment.level);
                const reasons = Object.entries(assessment.observations).map(([key, value]) => describeObservation(key, value)).filter(Boolean);
                return <li key={assessment.hazard}><div className="assessment-heading"><span><Icon name={assessment.hazard}/>{tr(HAZARDS.find((h) => h.id === assessment.hazard)?.th)}</span><span className={"risk-badge level-" + (assessment.level ?? "NONE")}>{tr(info.th)}</span></div>{tr(reasons.length > 0 && <p>{tr(reasons.join(" · "))}</p>)}{tr(assessment.notices.map((notice) => <p key={notice} className="data-notice">{tr(notice)}</p>))}<small>{tr("ที่มา: ")}{tr(assessment.sources.join(", ") || "ยังไม่มีข้อมูลแหล่งที่มา")}</small></li>;
            }))}</ul>
              {tr(assessments.length === 0 && <p className="section-description">{tr("ยังไม่มีผลประเมินสำหรับพื้นที่นี้")}</p>)}
              {tr((hazard === "overview" || hazard === "rain_storm") && area.forecast.length > 0 && <div className="forecast-section"><h3>{tr("พยากรณ์ฝนรายวัน")}</h3><ul className="forecast-grid">{tr(area.forecast.map((day) => <li key={day.date}><span>{tr(new Date(day.date).toLocaleDateString(getLocale(), { weekday: "short", day: "numeric", month: "short", timeZone: "Asia/Bangkok" }))}</span><Icon name="rain_storm"/><strong>{tr(day.rain_mm?.toFixed(1) ?? "—")}<small>{tr(" มม.")}</small></strong><span className={"risk-badge level-" + (day.level ?? "NONE")}>{tr(levelInfo(day.level).th)}</span>{tr(day.cond_th && <small>{tr(day.cond_th)}</small>)}</li>))}</ul><p className="source-note">{tr("กรมอุตุนิยมวิทยา (WRF) \u00B7 ")}{tr(formatTime(area.forecast_updated_at))}</p><button type="button" className="text-button" onClick={showWeatherImages}>{tr("ดูเรดาร์ฝนและแผนที่คาดการณ์ฝน")}<Icon name="arrow"/></button></div>)}
              {tr((hazard === "overview" || hazard === "flood") && areaRivers && <button type="button" className={"river-callout " + (areaRivers.over ? "" : "is-calm")} onClick={openProvinceRivers}><Icon name="river"/><span><strong>{tr(areaRivers.over ? tr("สถานีวัดน้ำในจังหวัดนี้ล้นตลิ่ง ") + tr(areaRivers.over) + tr(" แห่ง") : tr("สถานีวัดน้ำในจังหวัดนี้ ") + tr(areaRivers.count) + tr(" แห่ง"))}</strong><small>{tr("ดูระดับน้ำและน้ำไหลผ่านรายชั่วโมงเฉพาะจังหวัดนี้")}</small></span><Icon name="arrow"/></button>)}
              {tr(SHELTER_HAZARDS.has(hazard) && <ShelterCard area={area.area} urgent={assessments.some((a) => SHELTER_HAZARDS.has(a.hazard) && (a.level === "WARNING" || a.level === "CRITICAL"))} onShowMap={showSheltersOnMap}/>)}
              {tr(hazard === "overview" && !!area.degraded_services.length && <p className="data-notice">{tr("ข้อมูลบางแหล่งไม่พร้อมใช้งาน: ")}{tr(area.degraded_services.join(", "))}</p>)}
              <p className="source-note">{tr("อัปเดต ")}{tr(formatTime(area.data_updated_at))}{tr(" (เวลาไทย)")}</p>
            </> : <div className="area-intro"><h2>{tr(areaIntroductions()[hazard].title)}</h2><p className="section-description">{tr(areaIntroductions()[hazard].description)}</p>{tr(!searchInHeader && <button type="button" className="text-button" onClick={() => searchInput.current?.focus()}>{tr("ค้นหาพื้นที่")}<Icon name="arrow"/></button>)}</div>)}
            {tr(!area && areaStatus === "idle" && layer && <div className="national-summary"><span className={"summary-dot " + (warningCount ? "has-warning" : "")}/><p>{tr(warningCount ? <><strong>{tr(number.format(warningCount))}{tr(" อำเภอ")}</strong>{tr(" อยู่ในระดับเตือนภัยหรือวิกฤต")}</> : "ไม่มีอำเภอในระดับเตือนภัยหรือวิกฤตในข้อมูลชุดนี้")}<small>{tr("ภาพรวม")}{tr(itemTh(hazard))}{tr("ทั่วประเทศ")}</small></p></div>)}
            {tr(!area && areaStatus === "idle" && hazard === "overview" && nationalWarnings.length > 0 && <div className="official-warning official-warning--regions"><strong><Icon name="info"/>{tr("ประกาศทางการ · กรมอุตุนิยมวิทยา")}</strong>{tr(nationalWarnings.map((w) => <div key={w.title}><p className="official-warning-title">{tr(w.url ? <a href={w.url} target="_blank" rel="noreferrer">{tr(w.title)}</a> : w.title)}</p>{tr(!!w.by_region?.length && <ul className="warning-regions">{tr(w.by_region.map((r) => <li key={r.from + r.level + r.areas.join()}><span className={"risk-badge level-" + r.level}>{tr(levelInfo(r.level).th)}</span><span><b>{tr(r.areas.map((a) => tr(a)).join(" · "))}</b><small>{tr(dayRange(r.from, r.to))}{tr(" · ")}{tr(r.hazards.map((h) => itemTh(h)).join(", "))}</small></span></li>))}</ul>)}{tr(!!w.rain_days?.length && <><p className="official-warning-title">{tr("รายวันตามประกาศ")}</p><ul className="warning-regions">{tr(w.rain_days.map((d) => <li key={d.day + d.very_heavy}><span className={"risk-badge level-" + (d.very_heavy ? "WARNING" : "WATCH")}>{tr(d.very_heavy ? "ฝนตกหนักมาก" : "ฝนตกหนัก")}</span><span title={d.provinces.join(", ")}><b>{tr(dayRange(d.day, d.day))}</b><small>{tr(d.provinces.length <= 6 ? d.provinces.join(", ") : number.format(d.provinces.length) + tr(" จังหวัด"))}</small></span></li>))}</ul></>)}</div>))}<small>{tr("ระดับสีเป็นการประเมินของทันภัยจากประกาศ")}</small></div>)}
            {tr(!area && areaStatus === "idle" && hazard === "overview" && overviewBreakdown.length > 0 && <div className="overview-breakdown"><h3>{tr("อำเภอที่เฝ้าระวังขึ้นไป แยกตามภัยที่หนักที่สุด")}</h3><ul>{tr(overviewBreakdown.map(([id, n]) => <li key={id}><button type="button" onClick={() => openItem(id)}><Icon name={id}/><span>{tr(itemTh(id))}</span><strong>{tr(number.format(n))}</strong><Icon name="arrow"/></button></li>))}</ul></div>)}
          </section>

          {tr(view === "dams" && <section ref={pointPanel} className="search-panel dam-panel dam-panel--dams" aria-label={tr("ข้อมูลเขื่อน")} tabIndex={0}>
            {tr(locationScope)}
            {tr(damDetail ? <DamDetail dam={damDetail} onClose={() => setSelectedDam(null)}/> : <>
              <div className="area-intro"><h2>{tr("เขื่อนขนาดใหญ่")}{tr(areaProvince ? tr(" \u00B7 จ.") + tr(areaProvince.name) + tr("") : "ทั่วประเทศ")}</h2>
              <p className="section-description">{tr("แตะชื่อเขื่อนหรือวงกลมบนแผนที่ เพื่อดูว่าวันนี้ระบายน้ำเท่าไหร่")}</p>
              </div>
              {tr(chaoPhrayaDam && <div className="diversion-dam">
                <div><strong>{tr("เขื่อนเจ้าพระยา · จ.ชัยนาท")}</strong><span className="diversion-dam-badge">{tr("เขื่อนทดน้ำ")}</span></div>
                <p>{tr("ไม่มีอ่างเก็บน้ำ จึงไม่มีค่า % ความจุเหมือนเขื่อนขนาดใหญ่ ")}{tr(chaoPhrayaDam.discharge_m3s != null ? <>{tr("ปล่อยน้ำลงท้ายเขื่อน ")}<strong>{tr(number.format(Math.round(chaoPhrayaDam.discharge_m3s)))}{tr(" ลบ.ม./วินาที")}</strong>{tr(" (สถานี C.13, ")}{tr(riverTime(chaoPhrayaDam.time))})</> : tr("น้ำที่ปล่อยวัดที่สถานีท้ายเขื่อน C.13"))}</p>
                <button type="button" className="text-button" onClick={openChaoPhrayaDam}>{tr("ดูระดับน้ำและกราฟท้ายเขื่อน")}<Icon name="arrow"/></button>
              </div>)}
            </>)}
            {tr(!!visibleDams.length && <>
              <div className="dam-sort" role="group" aria-label={tr("เรียงรายชื่อเขื่อน")}>
                <button type="button" className={damSort === "storage" ? "is-active" : ""} aria-pressed={damSort === "storage"} onClick={() => setDamSort("storage")}>{tr("น้ำในอ่างมากสุด")}</button>
                <button type="button" className={damSort === "release" ? "is-active" : ""} aria-pressed={damSort === "release"} onClick={() => setDamSort("release")}>{tr("ระบายน้ำมากสุด")}</button>
              </div>
              <div className="dam-list-context"><span>{tr(number.format(visibleDams.length))}{tr(" เขื่อน")}</span><span>{tr("เลื่อนดูข้อมูลในช่องนี้")}</span></div>
              <ul className="dam-list">{tr(sortedDams.map((dam) => <DamRow key={damKey(dam)} dam={dam} active={damKey(dam) === selectedDam} sortBy={damSort} onSelect={chooseDam}/>))}</ul>
            </>)}
            {tr(areaProvince && dams.status === "success" && !visibleDams.length && <p className="search-feedback">{tr("ยังไม่มีข้อมูลเขื่อนขนาดใหญ่ในจังหวัดนี้")}</p>)}
          </section>)}

          {tr(view === "rivers" && <section ref={pointPanel} className="search-panel dam-panel" aria-label={tr("ข้อมูลแม่น้ำ")}>
            {tr(locationScope)}
            {tr(riverDetail ? <RiverDetail river={riverDetail} onClose={() => setSelectedRiver(null)}/> : <>
              <div className="area-intro"><h2>{tr("ระดับน้ำในแม่น้ำ")}{tr(riverProvince && tr(" \u00B7 จ.") + tr(riverProvince.name) + tr(""))}</h2>
              <p className="section-description">{tr("แตะสถานีเพื่อดูน้ำไหลผ่าน ระดับน้ำเทียบตลิ่ง และกราฟย้อนหลัง 3 วัน")}</p>
              </div>
            </>)}
            {tr(!!rivers.list.length && <>
              <div className="dam-sort river-sort" role="group" aria-label={tr("เลือกรายการสถานี")}>
                <button type="button" className={riverSort === "chain" ? "is-active" : ""} aria-pressed={riverSort === "chain"} onClick={() => setRiverSort("chain")}>{tr(riverProvince ? "ทุกสถานีในจังหวัด" : "เจ้าพระยา")}</button>
                <button type="button" className={riverSort === "overflow" ? "is-active" : ""} aria-pressed={riverSort === "overflow"} onClick={() => setRiverSort("overflow")}>{tr("ล้นตลิ่ง")}</button>
                <button type="button" className={riverSort === "flow" ? "is-active" : ""} aria-pressed={riverSort === "flow"} onClick={() => setRiverSort("flow")}>{tr("น้ำไหลมากสุด")}</button>
              </div>
              {tr(riverSort === "chain" && !riverProvince && <p className="section-description">{tr("เรียงจากเหนือลงใต้ ตามรายงานของกรมชลประทาน")}</p>)}
              {tr(sortedRivers.length === 0 ? <p className="search-feedback">{tr(riverSort === "overflow" ? "ตอนนี้ไม่มีสถานีที่น้ำล้นหรือใกล้ล้นตลิ่ง" : riverProvince ? "ยังไม่มีข้อมูลสถานีวัดน้ำในจังหวัดนี้" : "ไม่มีข้อมูล")}</p>
                    : <ul className="dam-list">{tr(sortedRivers.map((r) => <RiverRow key={riverKey(r)} river={r} active={riverKey(r) === selectedRiver} onSelect={chooseRiver}/>))}</ul>)}
            </>)}
          </section>)}

          {tr(view === "water-quality" && <section ref={pointPanel} className="search-panel dam-panel" aria-label={tr("ข้อมูลคุณภาพน้ำ")}>
            {tr(locationScope)}
            <WaterQualityPanel stations={visibleQuality} selected={selectedQuality} onSelect={chooseQuality} onClose={() => setSelectedQuality(null)} status={quality.status} degraded={quality.degraded} source={quality.source} onRetry={() => setRevision(n => n + 1)}/>
          </section>)}

          <FloatingMascot topic={view === "hazard" ? hazard : view} status={asking ? "waiting" : answer?.intent === "error" ? "error" : answer ? "answered" : "idle"}>
            {chat.length > 0 && <div className="chat-log" aria-live="polite">
              {chat.map((turn, i) => <div key={i} className="chat-turn">
                <p className="chat-question">{turn.asked}</p>
                {turn.answer ? answerView(turn.answer) : <p className="answer-text">{tr("กำลังตรวจสอบข้อมูลและเรียบเรียงคำตอบ…")}</p>}
              </div>)}
              <div ref={chatEnd}/>
            </div>}
            <form onSubmit={onAsk}><label htmlFor="ask">{tr(answer?.intent === "clarify" ? "ตอบน้องทันภัยได้เลย" : "อยากรู้สถานการณ์เรื่องไหน?")}</label><div className="ask-input"><input id="ask" ref={questionInput} autoComplete="off" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder={tr(answer?.intent === "clarify" ? "พิมพ์ชื่ออำเภอหรือจังหวัด เช่น ธัญบุรี" : "เช่น น้ำจะท่วมอำเภอเสนาไหม")} maxLength={500}/><button className="primary-button" disabled={asking || !question.trim()} aria-label={tr("ส่งคำถาม")}>{tr(asking ? <span className="spinner"/> : <Icon name="arrow"/>)}</button></div></form>
            {chat.length > 0 && !asking && <button type="button" className="text-button chat-clear" onClick={() => setChat([])}>{tr("เริ่มแชทใหม่")}</button>}
            {tr(chat.length === 0 && <div className="question-suggestions">{tr(["อำเภอเสนาเสี่ยงน้ำท่วมไหม", "เขื่อนภูมิพลมีน้ำเท่าไหร่", "เขื่อนเจ้าพระยาระบายน้ำเท่าไหร่"].map((example) => <button key={example} onClick={() => { setQuestion(example); questionInput.current?.focus(); }}>{tr(example)}</button>))}</div>)}
          </FloatingMascot>
        </aside>
      </div>
    </>);
}
