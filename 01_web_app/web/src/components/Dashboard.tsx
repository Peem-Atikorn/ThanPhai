"use client";

import dynamic from "next/dynamic";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, AreaBoundary, AreaHazards, AskAnswer, Dam, damBadge, damColor, describeObservation, Hazard, HAZARDS, levelInfo, LEVELS, mcmPerDayToM3s, River, riverColor, RIVER_STATUS } from "@/lib/api";
import Icon from "@/components/Icon";
import DamHistoryChart from "@/components/DamHistoryChart";
import MediaPanel from "@/components/MediaPanel";
import EventsPanel from "@/components/EventsPanel";
import { RiverDetail, RiverRow, riverTime, RiverSort, sortRivers } from "@/components/RiverPanel";

const HazardMap = dynamic(() => import("@/components/HazardMap"), {
  ssr: false,
  loading: () => <div className="map-loading" role="status"><span className="spinner" />กำลังโหลดแผนที่…</div>,
});
type Layer = Awaited<ReturnType<typeof api.layer>>;
type SearchArea = Awaited<ReturnType<typeof api.search>>[number];
const number = new Intl.NumberFormat("th-TH");
const prefix = (level: string) => level === "province" ? "จ." : level === "tambon" ? "ต." : "อ.";
const hazardDescriptions: Record<Hazard, string> = {
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

function formatTime(iso: string | null | undefined) {
  if (!iso || Number.isNaN(new Date(iso).getTime())) return "ยังไม่มีข้อมูล";
  return new Date(iso).toLocaleString("th-TH", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Bangkok" });
}

const DAM_LEGEND: { th: string; color: string; match: (d: Dam) => boolean }[] = [
  { th: "เกินระดับกักเก็บปกติ", color: "#ad2337", match: (d) => d.status === "HIGH" && (d.storage_percent ?? 0) > 100 },
  { th: "ใกล้เต็มอ่าง", color: "#e07b16", match: (d) => d.status === "HIGH" && (d.storage_percent ?? 0) <= 100 },
  { th: "น้ำปกติ", color: "#2f7fb5", match: (d) => d.status === "NORMAL" },
  { th: "น้ำใช้การได้เหลือน้อย", color: "#d4a600", match: (d) => d.status === "LOW_CRITICAL" },
  { th: "ไม่มีข้อมูล", color: "#8a97a3", match: (d) => d.status === "UNKNOWN" },
];
const damKey = (d: Dam) => d.id ?? d.name_th;
const mcm = (v: number | null, digits = 2) => v == null ? "—" : v.toLocaleString("th-TH", { minimumFractionDigits: digits, maximumFractionDigits: digits });
const m3s = (v: number | null) => v == null ? null : number.format(Math.round(mcmPerDayToM3s(v)));
/** Linear interpolation through (x, y) stops, for point sizes on the map. */
function scale(value: number, stops: [number, number][]) {
  if (value <= stops[0][0]) return stops[0][1];
  for (let i = 1; i < stops.length; i++) {
    const [x0, y0] = stops[i - 1];
    const [x1, y1] = stops[i];
    if (value <= x1) return y0 + ((value - x0) / (x1 - x0)) * (y1 - y0);
  }
  return stops[stops.length - 1][1];
}
// Circle area follows the water: sqrt of reservoir storage (million m³) or river flow (m³/s).
const damRadius = (d: Dam) => scale(Math.sqrt(d.storage_mcm ?? 0), [[0, 6], [40, 11], [135, 20]]);
const riverRadius = (r: River) => scale(Math.sqrt(r.discharge_m3s ?? 0), [[0, 4], [10, 6], [45, 15]]);
const riverKey = (r: River) => r.id ?? r.code;
const RID_HOURLY = "https://hyd-app.rid.go.th/hydro5h.html";
type View = "hazard" | "dams" | "rivers";
const damDate = (d: string | null) => d ? new Date(d).toLocaleDateString("th-TH", { dateStyle: "medium", timeZone: "Asia/Bangkok" }) : "—";

function DamRow({ dam, active, sortBy, onSelect }: { dam: Dam; active: boolean; sortBy: "storage" | "release"; onSelect: (d: Dam) => void }) {
  const pct = dam.storage_percent;
  return (
    <li>
      <button type="button" className={"dam-row " + (active ? "is-active" : "")} aria-pressed={active} onClick={() => onSelect(dam)}>
        <span className="dam-name"><strong>เขื่อน{dam.name_th}</strong><small>จ.{dam.province_th}{sortBy === "release" ? <> · มีน้ำ {pct != null ? pct.toFixed(0) + "%" : "—"}</> : dam.storage_mcm != null && <> · {number.format(Math.round(dam.storage_mcm))} ล้าน ลบ.ม.</>}</small></span>
        {sortBy === "release"
          ? <span className="dam-value"><strong>{mcm(dam.released_mcm)}</strong><small>ล้าน ลบ.ม./วัน</small></span>
          : <span className="dam-value"><strong>{pct != null ? pct.toFixed(0) + "%" : "—"}</strong><span className={"risk-badge " + damBadge(dam)}>{dam.status_th}</span></span>}
        <span className="dam-bar" aria-hidden><span style={{ width: Math.min(pct ?? 0, 120) / 1.2 + "%", background: damColor(dam) }} /></span>
      </button>
    </li>
  );
}

function DamDetail({ dam, onClose }: { dam: Dam; onClose: () => void }) {
  const flows: { label: string; value: number | null; note?: string }[] = [
    { label: "ระบายออกวันนี้", value: dam.released_mcm },
    { label: "น้ำไหลเข้าวันนี้", value: dam.inflow_mcm },
    { label: "ระบายผ่านทางน้ำล้น", value: dam.spilled_mcm },
  ];
  return (
    <div className="dam-detail">
      <div className="area-title">
        <span className="area-icon" style={{ color: damColor(dam) }}><Icon name="dam" /></span>
        <div><h2>เขื่อน{dam.name_th}</h2><p>จ.{dam.province_th}{dam.basin_th && <> · {dam.basin_th.startsWith("ลุ่มน้ำ") ? dam.basin_th : "ลุ่มน้ำ" + dam.basin_th}</>}</p></div>
        <button className="icon-button" aria-label="ปิดรายละเอียดเขื่อน" onClick={onClose}><Icon name="close" /></button>
      </div>
      <div className="dam-storage">
        <div><strong>{dam.storage_percent != null ? dam.storage_percent.toFixed(0) + "%" : "—"}</strong><span className={"risk-badge " + damBadge(dam)}>{dam.status_th}</span></div>
        <span className="dam-bar" aria-hidden><span style={{ width: Math.min(dam.storage_percent ?? 0, 120) / 1.2 + "%", background: damColor(dam) }} /></span>
        <p>มีน้ำ {mcm(dam.storage_mcm, 0)} จากระดับกักเก็บปกติ {mcm(dam.normal_storage_mcm, 0)} ล้าน ลบ.ม.{dam.max_storage_mcm != null && <> (รับได้สูงสุด {mcm(dam.max_storage_mcm, 0)})</>}</p>
        {dam.usable_percent != null && <p>น้ำที่นำมาใช้ได้ {dam.usable_percent.toFixed(0)}%</p>}
      </div>
      <ul className="dam-flows">
        {flows.map((f) => <li key={f.label}><span>{f.label}</span><strong>{mcm(f.value)}<small> ล้าน ลบ.ม.</small></strong>{f.value != null && f.value > 0 && <small>เฉลี่ยราว {m3s(f.value)} ลบ.ม./วินาที</small>}</li>)}
      </ul>
      <div className="dam-totals">
        <h3>สะสมตั้งแต่ 1 มกราคม</h3>
        <p>น้ำไหลเข้า {mcm(dam.inflow_acc_mcm, 0)} ล้าน ลบ.ม.{dam.inflow_avg_year_mcm != null && dam.inflow_avg_year_mcm > 0 && dam.inflow_acc_mcm != null && <> ({Math.round((dam.inflow_acc_mcm / dam.inflow_avg_year_mcm) * 100)}% ของค่าเฉลี่ยทั้งปี)</>}</p>
        <p>ระบายออก {mcm(dam.released_acc_mcm, 0)} ล้าน ลบ.ม.</p>
      </div>
      {dam.id && <DamHistoryChart damId={dam.id} />}
      <p className="source-note">ข้อมูลรายวันของวันที่ {damDate(dam.date)} · ThaiWater (สสน.){dam.agency_th && <>, รายงานโดย {dam.agency_th}</>}</p>
      <p className="source-note">ปริมาณต่อวินาทีเป็นค่าเฉลี่ยจากยอดทั้งวัน ไม่ใช่อัตราระบาย ณ ขณะนี้</p>
    </div>
  );
}

export default function Dashboard() {
  const [hazard, setHazard] = useState<Hazard>("flood");
  const [revision, setRevision] = useState(0);
  const [layerState, setLayerState] = useState<{ key: string; data: Layer | null; error: boolean } | null>(null);
  const layerKey = hazard + ":" + revision;
  const layer = layerState?.key === layerKey || layerState?.key.startsWith(hazard + ":") ? layerState?.data ?? null : null;
  const layerError = layerState?.key === layerKey && layerState.error;
  const layerLoading = layerState?.key !== layerKey;
  const [selected, setSelected] = useState<string | null>(null);
  const [area, setArea] = useState<AreaHazards | null>(null);
  const [areaStatus, setAreaStatus] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [focus, setFocus] = useState<{ lat: number; lon: number; zoom?: number } | null>(null);
  const [outline, setOutline] = useState<AreaBoundary | null>(null);
  const outlineRequest = useRef(0);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchArea[] | null>(null);
  const [searchStatus, setSearchStatus] = useState<"idle" | "loading" | "error" | "success">("idle");
  const searchRequest = useRef(0);
  const searchInput = useRef<HTMLInputElement>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState<AskAnswer | null>(null);
  const [asking, setAsking] = useState(false);
  const questionInput = useRef<HTMLInputElement>(null);
  const [dams, setDams] = useState<{ list: Dam[]; status: "loading" | "error" | "success" }>({ list: [], status: "loading" });
  const [view, setView] = useState<View>("hazard");
  const [rivers, setRivers] = useState<{ list: River[]; status: "loading" | "error" | "success" }>({ list: [], status: "loading" });
  const [selectedRiver, setSelectedRiver] = useState<string | null>(null);
  const [riverSort, setRiverSort] = useState<RiverSort>("chain");
  // Set when the river view is opened from a province's card: list and map show that province only.
  const [riverProvince, setRiverProvince] = useState<{ code: string; name: string } | null>(null);
  const visibleRivers = useMemo(() => riverProvince ? rivers.list.filter((r) => r.province_code === riverProvince.code) : rivers.list,
    [rivers.list, riverProvince]);
  const [selectedDam, setSelectedDam] = useState<string | null>(null);
  const [damSort, setDamSort] = useState<"storage" | "release">("storage");
  const damPoints = useMemo<GeoJSON.FeatureCollection>(() => ({
    type: "FeatureCollection",
    features: dams.list.filter((d) => d.lat != null && d.lon != null).map((d) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [d.lon!, d.lat!] },
      properties: { id: damKey(d), color: damColor(d), radius: damRadius(d) },
    })),
  }), [dams.list]);
  const riverPoints = useMemo<GeoJSON.FeatureCollection>(() => ({
    type: "FeatureCollection",
    features: visibleRivers.map((r) => ({
      type: "Feature",
      geometry: { type: "Point", coordinates: [r.lon, r.lat] },
      properties: { id: riverKey(r), color: riverColor(r), radius: riverRadius(r) },
    })),
  }), [visibleRivers]);
  const riverDetail = rivers.list.find((r) => riverKey(r) === selectedRiver) ?? null;
  // Within one province every station is listed (most provinces have a handful), not only the top 30.
  const sortedRivers = riverProvince && riverSort === "flow"
    ? [...visibleRivers].sort((a, b) => (b.discharge_m3s ?? -1) - (a.discharge_m3s ?? -1))
    : sortRivers(visibleRivers, riverSort);
  const belowChaoPhrayaDam = rivers.list.find((r) => r.code === "C.13");
  const damDetail = dams.list.find((d) => damKey(d) === selectedDam) ?? null;
  const sortedDams = damSort === "release" ? [...dams.list].sort((a, b) => (b.released_mcm ?? -1) - (a.released_mcm ?? -1)) : dams.list;
  const totalRelease = dams.list.reduce((sum, d) => sum + (d.released_mcm ?? 0), 0);

  useEffect(() => {
    let cancelled = false;
    api.dams()
      .then((d) => !cancelled && setDams({ list: d.dams, status: "success" }))
      .catch(() => !cancelled && setDams((old) => ({ list: old.list, status: "error" })));
    return () => { cancelled = true; };
  }, [revision]);

  useEffect(() => {
    let cancelled = false;
    api.rivers()
      .then((d) => !cancelled && setRivers({ list: d.rivers, status: "success" }))
      .catch(() => !cancelled && setRivers((old) => ({ list: old.list, status: "error" })));
    return () => { cancelled = true; };
  }, [revision]);

  useEffect(() => {
    let cancelled = false;
    api.layer(hazard)
      .then((data) => !cancelled && setLayerState({ key: layerKey, data, error: false }))
      .catch(() => !cancelled && setLayerState({ key: layerKey, data: null, error: true }));
    return () => { cancelled = true; };
  }, [hazard, layerKey]);

  // Live data changes through the day: reload every 10 minutes, and every 30 s while the backend is still
  // loading satellite/forecast data after a restart (otherwise the map kept the half-loaded first answer).
  const pending = !!layer?.meta.inputs_pending?.length;
  useEffect(() => {
    const id = window.setInterval(() => {
      if (document.visibilityState === "visible") setRevision((value) => value + 1);
    }, pending ? 30_000 : 600_000);
    return () => window.clearInterval(id);
  }, [pending]);

  const latestArea = useRef(0);
  const select = useCallback((code: string, mapCode: string | null = code) => {
    const request = ++latestArea.current;
    setSelected(mapCode);
    setArea(null);
    setAreaStatus("loading");
    api.area(code)
      .then((data) => {
        if (latestArea.current === request) { setArea(data); setAreaStatus("success"); }
      })
      .catch(() => latestArea.current === request && setAreaStatus("error"));
  }, []);
  const selectedCode = useRef<string | null>(null);
  const selectFromMap = useCallback((code: string) => {
    outlineRequest.current++;
    setOutline(null);  // a tapped district is outlined by the map's own selection layer
    selectedCode.current = code;
    select(code);
  }, [select]);

  function chooseArea(item: SearchArea | NonNullable<AskAnswer["area"]>) {
    selectedCode.current = item.code;
    select(item.code, item.level === "province" ? null : item.level === "tambon" ? item.code.slice(0, 6) : item.code);
    // Provinces and sub-districts get their own outline, and the map fits to it; districts use the map's selection.
    const request = ++outlineRequest.current;
    setOutline(null);
    if (item.level === "amphoe") {
      setFocus({ lat: item.lat, lon: item.lon, zoom: 9 });
    } else {
      api.boundary(item.code)
        .then((b) => request === outlineRequest.current && setOutline(b))
        .catch(() => request === outlineRequest.current && setFocus({ lat: item.lat, lon: item.lon, zoom: item.level === "province" ? 7 : 11 }));
    }
    setResults(null);
    setSearchStatus("idle");
  }

  function chooseDam(dam: Dam) {
    setSelectedDam(damKey(dam));
    revealPanel();
    if (dam.lat != null && dam.lon != null) setFocus({ lat: dam.lat, lon: dam.lon, zoom: 8 });
  }
  // The detail card opens at the top of the side panel; bring it on screen when the list or map scrolled it away.
  const pointPanel = useRef<HTMLElement>(null);
  const revealPanel = useCallback(() => {
    requestAnimationFrame(() => {
      const el = pointPanel.current;
      if (!el) return;
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
    if (!areaRivers) return;
    setView("rivers");
    setRiverProvince({ code: areaRivers.code, name: areaRivers.name });
    setRiverSort(areaRivers.over ? "overflow" : "flow");
    setSelectedRiver(null);
    revealPanel();
    // Outline the province and fit the map to it, also when the card came from a tapped district.
    const request = ++outlineRequest.current;
    api.boundary(areaRivers.code).then((b) => request === outlineRequest.current && setOutline(b)).catch(() => {});
  }

  // The image panel mounts with the rain view; scroll to it after that render, not on a timer.
  const [imagesRequest, setImagesRequest] = useState(0);
  function showWeatherImages() {
    setView("hazard");
    setHazard("rain_storm");
    setImagesRequest((n) => n + 1);
  }
  useEffect(() => {
    if (!imagesRequest) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    document.getElementById("weather-images")?.scrollIntoView({ block: "start", behavior: reduce ? "auto" : "smooth" });
  }, [imagesRequest]);

  function chooseRiver(river: River) {
    setSelectedRiver(riverKey(river));
    setFocus({ lat: river.lat, lon: river.lon, zoom: 9 });
    revealPanel();
  }
  const selectRiverFromMap = useCallback((id: string) => { setSelectedRiver(id); revealPanel(); }, [revealPanel]);

  async function onSearch(event: FormEvent) {
    event.preventDefault();
    if (!query.trim()) return;
    const request = ++searchRequest.current;
    setSearchStatus("loading");
    setResults(null);
    try {
      const found = await api.search(query.trim());
      if (request === searchRequest.current) { setResults(found); setSearchStatus("success"); }
    } catch {
      if (request === searchRequest.current) setSearchStatus("error");
    }
  }

  async function onAsk(event: FormEvent) {
    event.preventDefault();
    if (!question.trim() || asking) return;
    setAsking(true);
    setAnswer(null);
    try {
      const response = await api.ask(question.trim());
      setAnswer(response);
      if (response.area) chooseArea(response.area);
    } catch {
      setAnswer({ text: "ยังตอบคำถามไม่ได้ในขณะนี้ กรุณาลองถามอีกครั้ง", intent: "error", area: null, sources: [] });
    } finally { setAsking(false); }
  }

  const counts = LEVELS.map((level) => ({ ...level, n: layer?.features.filter((f) => (f.properties?.level ?? "NONE") === level.id).length ?? 0 }));
  const warningCount = counts.filter((l) => l.id === "CRITICAL" || l.id === "WARNING").reduce((sum, l) => sum + l.n, 0);
  const warnings = area?.official_warnings ?? [];

  return (
    <>
      <div className="dashboard-grid">
        <a className="mobile-search-link" href="#area-search"><Icon name="search" />ค้นหาจังหวัด อำเภอ หรือตำบล<Icon name="arrow" /></a>
        <section className="map-panel" aria-label="ภาพรวมความเสี่ยง">
          <div className="hazard-toolbar" role="group" aria-label="เลือกประเภทภัย">
            {HAZARDS.map((item) => { const active = view === "hazard" && hazard === item.id; return <button key={item.id} type="button" className={"hazard-button " + (active ? "is-active" : "")} aria-pressed={active} disabled={!item.ready} onClick={() => { setHazard(item.id); setView("hazard"); }}><Icon name={item.id} /><span>{item.th}</span>{!item.ready && <small>เร็ว ๆ นี้</small>}</button>; })}
            <button type="button" className={"hazard-button " + (view === "dams" ? "is-active" : "")} aria-pressed={view === "dams"} onClick={() => setView("dams")}><Icon name="dam" /><span>เขื่อน</span></button>
            <button type="button" className={"hazard-button " + (view === "rivers" ? "is-active" : "")} aria-pressed={view === "rivers"} onClick={() => { setView("rivers"); setRiverProvince(null); }}><Icon name="river" /><span>แม่น้ำ</span></button>
          </div>
          <div className="map-heading">
            {view === "dams"
              ? <div><h2>ปริมาณน้ำและการระบายน้ำของเขื่อน</h2><p>เขื่อนขนาดใหญ่ทั่วประเทศ · ขนาดวงกลมตามปริมาณน้ำในอ่าง</p></div>
              : view === "rivers"
              ? <div><h2>ระดับน้ำและน้ำไหลผ่านในแม่น้ำ</h2><p>สถานีวัดน้ำรายชั่วโมง · ขนาดวงกลมตามปริมาณน้ำไหลผ่าน</p></div>
              : <div><h2>แผนที่ความเสี่ยง{HAZARDS.find((h) => h.id === hazard)?.th}</h2><p>{hazardDescriptions[hazard]}</p></div>}
            {view === "hazard" && <button type="button" className="images-link" onClick={showWeatherImages}><Icon name="radar" />เรดาร์ · คาดการณ์ฝน · พายุ</button>}
            <button className="icon-button" onClick={() => setRevision((value) => value + 1)} disabled={layerLoading} aria-label="โหลดข้อมูลแผนที่ใหม่" title="โหลดข้อมูลแผนที่ใหม่"><Icon name="refresh" className={layerLoading ? "spinning" : ""} /></button>
          </div>
          <div className="map-canvas">
            <HazardMap data={layer} selected={selected} onSelect={selectFromMap} focus={focus} outline={outline} mode={view === "hazard" ? "hazard" : "points"}
              points={view === "dams" ? damPoints : view === "rivers" ? riverPoints : null}
              selectedPoint={view === "dams" ? selectedDam : selectedRiver}
              onSelectPoint={view === "dams" ? selectDamFromMap : selectRiverFromMap}
              label={view === "dams" ? "แผนที่เขื่อนขนาดใหญ่" : view === "rivers" ? "แผนที่สถานีวัดน้ำในแม่น้ำ" : undefined} />
            <div className="map-hint"><Icon name="pin" /><span>{view === "dams" ? "แตะวงกลมเพื่อดูข้อมูลเขื่อน" : view === "rivers" ? "แตะวงกลมเพื่อดูข้อมูลสถานีวัดน้ำ" : "แตะพื้นที่เพื่อดูรายละเอียด"}</span></div>
            <button type="button" className="map-reset" onClick={() => setFocus({ lat: 13.4, lon: 100.9, zoom: 5 })}><Icon name="map" />ภาพรวมประเทศ</button>
            {view === "hazard" && <>
              {layerLoading && <div className="map-notice" role="status"><span className="spinner" />กำลังโหลดข้อมูลความเสี่ยง…</div>}
              {layerError && <div className="map-notice error-notice" role="alert"><Icon name="info" /><div><strong>โหลดข้อมูลความเสี่ยงไม่สำเร็จ</strong><p>ตรวจสอบการเชื่อมต่อ แล้วลองอีกครั้ง</p></div><button className="text-button" onClick={() => setRevision((value) => value + 1)}>ลองใหม่</button></div>}
              {layer && layer.features.length === 0 && <div className="map-notice" role="status">ยังไม่มีข้อมูลความเสี่ยงสำหรับภัยประเภทนี้</div>}
            </>}
            {view === "dams" && dams.status === "loading" && <div className="map-notice" role="status"><span className="spinner" />กำลังโหลดข้อมูลเขื่อน…</div>}
            {view === "dams" && dams.status === "error" && !dams.list.length && <div className="map-notice error-notice" role="alert"><Icon name="info" /><div><strong>โหลดข้อมูลเขื่อนไม่สำเร็จ</strong><p>ตรวจสอบการเชื่อมต่อ แล้วลองอีกครั้ง</p></div><button className="text-button" onClick={() => setRevision((value) => value + 1)}>ลองใหม่</button></div>}
            {view === "rivers" && rivers.status === "loading" && <div className="map-notice" role="status"><span className="spinner" />กำลังโหลดข้อมูลแม่น้ำ…</div>}
            {view === "rivers" && rivers.status === "error" && !rivers.list.length && <div className="map-notice error-notice" role="alert"><Icon name="info" /><div><strong>โหลดข้อมูลแม่น้ำไม่สำเร็จ</strong><p>ตรวจสอบการเชื่อมต่อ แล้วลองอีกครั้ง</p></div><button className="text-button" onClick={() => setRevision((value) => value + 1)}>ลองใหม่</button></div>}
          </div>
          {view === "rivers" ? <div className="map-summary">
            <div className="legend-heading"><h3>สถานะน้ำเทียบตลิ่ง{riverProvince && ` · จ.${riverProvince.name}`}</h3><span>{rivers.list.length ? number.format(visibleRivers.length) + " สถานี" : "รอข้อมูล"}</span></div>
            <ul className="risk-legend">{RIVER_STATUS.map((c) => <li key={c.id}><span className="legend-dot" style={{ background: c.color }} /><span>{c.th}</span><strong>{rivers.list.length ? number.format(visibleRivers.filter((r) => r.status === c.id).length) : "—"}</strong></li>)}</ul>
            {belowChaoPhrayaDam?.discharge_m3s != null && <p className="dam-total-release">เขื่อนเจ้าพระยา (ชัยนาท) ปล่อยน้ำลงท้ายเขื่อน <strong>{number.format(Math.round(belowChaoPhrayaDam.discharge_m3s))} ลบ.ม./วินาที</strong> (สถานี C.13, {riverTime(belowChaoPhrayaDam.time)})</p>}
            <div className="data-timestamp"><Icon name="clock" /><span>ข้อมูลรายชั่วโมงจาก ThaiWater (สสน.) · <a href={RID_HOURLY} target="_blank" rel="noreferrer noopener">รายงานน้ำรายชั่วโมงของกรมชลประทาน</a></span></div>
          </div> : view === "dams" ? <div className="map-summary">
            <div className="legend-heading"><h3>สถานะน้ำในเขื่อน</h3><span>{dams.list.length ? number.format(dams.list.length) + " เขื่อน" : "รอข้อมูล"}</span></div>
            <ul className="risk-legend">{DAM_LEGEND.map((c) => <li key={c.th}><span className="legend-dot" style={{ background: c.color }} /><span>{c.th}</span><strong>{dams.list.length ? number.format(dams.list.filter(c.match).length) : "—"}</strong></li>)}</ul>
            {!!dams.list.length && <p className="dam-total-release">วันนี้ทุกเขื่อนรวมกันระบายน้ำ <strong>{mcm(totalRelease)} ล้าน ลบ.ม.</strong> (เฉลี่ยราว {m3s(totalRelease)} ลบ.ม./วินาที)</p>}
            <div className="data-timestamp"><Icon name="clock" /><span>ข้อมูลรายวันของวันที่ {damDate(dams.list[0]?.date ?? null)} · % เทียบกับระดับกักเก็บปกติ</span></div>
          </div> : <div className="map-summary">
            <div className="legend-heading"><h3>ระดับความเสี่ยง</h3><span>{layer ? number.format(layer.features.length) + " อำเภอ" : "รอข้อมูล"}</span></div>
            <ul className="risk-legend">{counts.map((level) => <li key={level.id}><span className="legend-dot" style={{ background: level.color }} /><span>{level.th}</span><strong>{layer ? number.format(level.n) : "—"}</strong></li>)}</ul>
            <div className="data-timestamp"><Icon name="clock" /><span>ข้อมูล ณ {formatTime(layer?.meta.data_updated_at)} (เวลาไทย){pending && " · กำลังโหลดข้อมูลดาวเทียมและพยากรณ์เพิ่มเติม"}</span></div>
            {!!layer?.meta.degraded_services.length && <p className="data-notice">ข้อมูลบางแหล่งไม่พร้อมใช้งาน: {layer.meta.degraded_services.join(", ")}</p>}
          </div>}
          {view === "hazard" && hazard === "rain_storm" && <div id="weather-images" className="media-anchor"><MediaPanel revision={revision} /></div>}
          {view === "hazard" && (hazard === "earthquake" || hazard === "tropical_cyclone") && <EventsPanel revision={revision} quakes={hazard === "earthquake"} />}
        </section>

        <aside className="detail-sidebar" aria-label="ค้นหาและดูรายละเอียดพื้นที่">
          <section className="search-panel">
            <h2>พื้นที่ที่คุณอยากรู้</h2><p className="section-description">ค้นหาจังหวัด อำเภอ หรือตำบล</p>
            <form onSubmit={onSearch} className="search-form" role="search">
              <label className="sr-only" htmlFor="area-search">ค้นหาพื้นที่</label>
              <div className="input-wrap"><Icon name="search" /><input id="area-search" ref={searchInput} placeholder="เช่น เสนา, เชียงใหม่" autoComplete="off" value={query} maxLength={120} onChange={(event) => { setQuery(event.target.value); searchRequest.current++; setResults(null); setSearchStatus("idle"); }} /></div>
              <button className="primary-button" disabled={!query.trim() || searchStatus === "loading"}>{searchStatus === "loading" ? <span className="spinner" /> : "ค้นหา"}</button>
            </form>
            <div aria-live="polite">
              {searchStatus === "loading" && <p className="search-feedback">กำลังค้นหาพื้นที่…</p>}
              {searchStatus === "error" && <p className="search-feedback data-notice">ค้นหาไม่สำเร็จ กรุณากดค้นหาเพื่อลองอีกครั้ง</p>}
              {results?.length === 0 && <p className="search-feedback">ไม่พบพื้นที่นี้ ลองใช้ชื่อจังหวัดหรืออำเภอเต็ม</p>}
            </div>
            {!!results?.length && <div className="search-results"><div className="results-heading"><span>พบ {results.length} พื้นที่</span><button className="icon-button" aria-label="ปิดผลการค้นหา" onClick={() => { setResults(null); searchInput.current?.focus(); }}><Icon name="close" /></button></div><ul>{results.map((item) => <li key={item.code}><button onClick={() => chooseArea(item)}><Icon name="pin" /><span><strong>{prefix(item.level)}{item.name_th}</strong><small>{item.parents.join(" · ") || "ประเทศไทย"}</small></span><Icon name="arrow" /></button></li>)}</ul></div>}
          </section>

          <section className="area-panel" aria-label="รายละเอียดพื้นที่" aria-busy={areaStatus === "loading"} hidden={view !== "hazard"}>
            {areaStatus === "loading" ? <div className="empty-area" role="status"><span className="spinner" /><h3>กำลังดูข้อมูลพื้นที่…</h3><p>รวบรวมระดับความเสี่ยงและข้อมูลล่าสุด</p></div> : areaStatus === "error" ? <div className="empty-area" role="alert"><Icon name="info" /><h3>ยังเปิดข้อมูลพื้นที่ไม่ได้</h3><p>กรุณาลองใหม่ หรือเลือกพื้นที่อื่น</p><button className="secondary-button" onClick={() => selectedCode.current && select(selectedCode.current, selected)}>ลองใหม่</button></div> : area ? <>
              <div className="area-title"><span className="area-icon"><Icon name="pin" /></span><div><h2>{prefix(area.area.level)}{area.area.name_th}</h2><p>{area.area.parents.join(" · ") || "ประเทศไทย"}</p></div></div>
              {area.area.level === "province" && <p className="data-notice">ระดับจังหวัดแสดงความเสี่ยงสูงสุดของอำเภอในจังหวัด</p>}
              {warnings.length > 0 && <div className="official-warning"><strong><Icon name="info" />ประกาศทางการ · กรมอุตุนิยมวิทยา</strong><ul>{warnings.map((w) => <li key={w.title}>{w.url ? <a href={w.url} target="_blank" rel="noreferrer">{w.title}</a> : w.title}</li>)}</ul><small>ประกาศระดับประเทศ โปรดตรวจสอบพื้นที่ที่ระบุในประกาศ</small></div>}
              <ul className="assessment-list">{area.assessments.map((assessment) => {
                const info = levelInfo(assessment.level);
                const reasons = Object.entries(assessment.observations).map(([key, value]) => describeObservation(key, value)).filter(Boolean);
                return <li key={assessment.hazard}><div className="assessment-heading"><span><Icon name={assessment.hazard} />{HAZARDS.find((h) => h.id === assessment.hazard)?.th}</span><span className={"risk-badge level-" + (assessment.level ?? "NONE")}>{info.th}</span></div>{reasons.length > 0 && <p>{reasons.join(" · ")}</p>}{assessment.notices.map((notice) => <p key={notice} className="data-notice">{notice}</p>)}<small>ที่มา: {assessment.sources.join(", ") || "ยังไม่มีข้อมูลแหล่งที่มา"}</small></li>;
              })}</ul>
              {area.assessments.length === 0 && <p className="section-description">ยังไม่มีผลประเมินสำหรับพื้นที่นี้</p>}
              {area.forecast.length > 0 && <div className="forecast-section"><h3>พยากรณ์ฝนรายวัน</h3><ul className="forecast-grid">{area.forecast.map((day) => <li key={day.date}><span>{new Date(day.date).toLocaleDateString("th-TH", { weekday: "short", day: "numeric", month: "short", timeZone: "Asia/Bangkok" })}</span><Icon name="rain_storm" /><strong>{day.rain_mm?.toFixed(1) ?? "—"}<small> มม.</small></strong><span className={"risk-badge level-" + (day.level ?? "NONE")}>{levelInfo(day.level).th}</span>{day.cond_th && <small>{day.cond_th}</small>}</li>)}</ul><p className="source-note">กรมอุตุนิยมวิทยา (WRF) · {formatTime(area.forecast_updated_at)}</p><button type="button" className="text-button" onClick={showWeatherImages}>ดูเรดาร์ฝนและแผนที่คาดการณ์ฝน<Icon name="arrow" /></button></div>}
              {areaRivers && <button type="button" className={"river-callout " + (areaRivers.over ? "" : "is-calm")} onClick={openProvinceRivers}><Icon name="river" /><span><strong>{areaRivers.over ? `สถานีวัดน้ำในจังหวัดนี้ล้นตลิ่ง ${areaRivers.over} แห่ง` : `สถานีวัดน้ำในจังหวัดนี้ ${areaRivers.count} แห่ง`}</strong><small>ดูระดับน้ำและน้ำไหลผ่านรายชั่วโมงเฉพาะจังหวัดนี้</small></span><Icon name="arrow" /></button>}
              {!!area.degraded_services.length && <p className="data-notice">ข้อมูลบางแหล่งไม่พร้อมใช้งาน: {area.degraded_services.join(", ")}</p>}
              <p className="source-note">อัปเดต {formatTime(area.data_updated_at)} (เวลาไทย)</p>
            </> : <div className="empty-area"><span className="empty-map-icon"><Icon name="map" width="32" height="32" /></span><h3>เริ่มจากพื้นที่ของคุณ</h3><p>แตะอำเภอบนแผนที่ หรือค้นหาชื่อพื้นที่<br />เพื่อดูความเสี่ยงทั้ง {HAZARDS.length} ประเภท</p><button className="text-button" onClick={() => searchInput.current?.focus()}>ค้นหาพื้นที่<Icon name="arrow" /></button></div>}
            {!area && areaStatus === "idle" && layer && <div className="national-summary"><span className={"summary-dot " + (warningCount ? "has-warning" : "")} /><p>{warningCount ? <><strong>{number.format(warningCount)} อำเภอ</strong> อยู่ในระดับเตือนภัยหรือวิกฤต</> : "ไม่มีอำเภอในระดับเตือนภัยหรือวิกฤตในข้อมูลชุดนี้"}<small>ภาพรวม{HAZARDS.find((h) => h.id === hazard)?.th}ทั่วประเทศ</small></p></div>}
          </section>

          {view === "dams" && <section ref={pointPanel} className="search-panel dam-panel" aria-label="ข้อมูลเขื่อน">
            {damDetail ? <DamDetail dam={damDetail} onClose={() => setSelectedDam(null)} /> : <>
              <h2>เขื่อนขนาดใหญ่ทั่วประเทศ</h2>
              <p className="section-description">แตะชื่อเขื่อนหรือวงกลมบนแผนที่ เพื่อดูว่าวันนี้ระบายน้ำเท่าไหร่</p>
            </>}
            {!!dams.list.length && <>
              <div className="dam-sort" role="group" aria-label="เรียงรายชื่อเขื่อน">
                <button type="button" className={damSort === "storage" ? "is-active" : ""} aria-pressed={damSort === "storage"} onClick={() => setDamSort("storage")}>น้ำในอ่างมากสุด</button>
                <button type="button" className={damSort === "release" ? "is-active" : ""} aria-pressed={damSort === "release"} onClick={() => setDamSort("release")}>ระบายน้ำมากสุด</button>
              </div>
              <ul className="dam-list">{sortedDams.map((dam) => <DamRow key={damKey(dam)} dam={dam} active={damKey(dam) === selectedDam} sortBy={damSort} onSelect={chooseDam} />)}</ul>
            </>}
          </section>}

          {view === "rivers" && <section ref={pointPanel} className="search-panel dam-panel" aria-label="ข้อมูลแม่น้ำ">
            {riverDetail ? <RiverDetail river={riverDetail} onClose={() => setSelectedRiver(null)} /> : <>
              <h2>ระดับน้ำในแม่น้ำ{riverProvince && ` · จ.${riverProvince.name}`}</h2>
              <p className="section-description">แตะสถานีเพื่อดูน้ำไหลผ่าน ระดับน้ำเทียบตลิ่ง และกราฟย้อนหลัง 3 วัน</p>
            </>}
            {riverProvince && <div className="filter-chip"><span>เฉพาะ จ.{riverProvince.name} · {visibleRivers.length} สถานี</span><button type="button" onClick={() => { setRiverProvince(null); setRiverSort("chain"); }}>ดูทั้งประเทศ<Icon name="close" /></button></div>}
            {!!rivers.list.length && <>
              <div className="dam-sort river-sort" role="group" aria-label="เลือกรายการสถานี">
                <button type="button" className={riverSort === "chain" ? "is-active" : ""} aria-pressed={riverSort === "chain"} onClick={() => setRiverSort("chain")}>เจ้าพระยา</button>
                <button type="button" className={riverSort === "overflow" ? "is-active" : ""} aria-pressed={riverSort === "overflow"} onClick={() => setRiverSort("overflow")}>ล้นตลิ่ง</button>
                <button type="button" className={riverSort === "flow" ? "is-active" : ""} aria-pressed={riverSort === "flow"} onClick={() => setRiverSort("flow")}>น้ำไหลมากสุด</button>
              </div>
              {riverSort === "chain" && <p className="section-description">เรียงจากเหนือลงใต้ ตามรายงานของกรมชลประทาน</p>}
              {sortedRivers.length === 0 ? <p className="search-feedback">{riverSort === "overflow" ? "ตอนนี้ไม่มีสถานีที่น้ำล้นหรือใกล้ล้นตลิ่ง" : "ไม่มีข้อมูล"}</p>
                : <ul className="dam-list">{sortedRivers.map((r) => <RiverRow key={riverKey(r)} river={r} active={riverKey(r) === selectedRiver} onSelect={chooseRiver} />)}</ul>}
            </>}
          </section>}

          <section className="assistant-panel">
            <div className="assistant-heading"><span className="assistant-icon"><Icon name="chat" /></span><div><h2>ถามทันภัย</h2><p>ช่วยอ่านข้อมูลความเสี่ยงให้เข้าใจง่าย</p></div></div>
            <form onSubmit={onAsk}><label htmlFor="ask">อยากรู้สถานการณ์เรื่องไหน?</label><div className="ask-input"><input id="ask" ref={questionInput} value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="เช่น น้ำจะท่วมอำเภอเสนาไหม" maxLength={500} /><button className="primary-button" disabled={asking || !question.trim()} aria-label="ส่งคำถาม">{asking ? <span className="spinner" /> : <Icon name="arrow" />}</button></div></form>
            {!answer && !asking && <div className="question-suggestions">{["อำเภอเสนาเสี่ยงน้ำท่วมไหม", "เขื่อนภูมิพลมีน้ำเท่าไหร่", "เขื่อนเจ้าพระยาระบายน้ำเท่าไหร่"].map((example) => <button key={example} onClick={() => { setQuestion(example); questionInput.current?.focus(); }}>{example}</button>)}</div>}
            <div aria-live="polite">{asking && <p className="answer-text">กำลังตรวจสอบข้อมูลและเรียบเรียงคำตอบ…</p>}{answer && <div className={"answer-text " + (answer.intent === "error" ? "data-notice" : "")}><p>{answer.text}</p>{answer.sources.length > 0 && <p className="source-note">ที่มา: {answer.sources.join(", ")}</p>}{answer.llm_used && <p className="source-note">เรียบเรียงโดย AI (Gemini) · ระดับเตือนภัยมาจากกฎของระบบ ไม่ได้ให้ AI ตัดสิน</p>}</div>}</div>
          </section>
        </aside>
      </div>
      <div className="disclaimer"><Icon name="shield" /><p>ทันภัยเป็นเครื่องมือประเมินความเสี่ยง ไม่ใช่ประกาศทางการ <strong>ให้ยึดประกาศจากหน่วยงานทางการเป็นหลัก</strong></p></div>
    </>
  );
}
