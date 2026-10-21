export type Hazard = "flood" | "rain_storm" | "fire" | "drought" | "air_quality" | "heat" | "earthquake";
export type Level = "NORMAL" | "WATCH" | "WARNING" | "CRITICAL" | null;

export const HAZARDS: { id: Hazard; th: string; ready: boolean }[] = [
  { id: "flood", th: "น้ำท่วม", ready: true },
  { id: "rain_storm", th: "ฝนหนัก/พายุ", ready: true },
  { id: "fire", th: "ไฟป่า/ไฟหญ้า", ready: true },
  { id: "drought", th: "ภัยแล้ง", ready: true },
  { id: "air_quality", th: "ฝุ่น PM2.5", ready: true },
  { id: "heat", th: "ความร้อนจัด", ready: true },
  { id: "earthquake", th: "แผ่นดินไหว", ready: true },
];

export const LEVELS: { id: Exclude<Level, null> | "NONE"; th: string; color: string }[] = [
  { id: "CRITICAL", th: "วิกฤต", color: "#d7263d" },
  { id: "WARNING", th: "เตือนภัย", color: "#f77f00" },
  { id: "WATCH", th: "เฝ้าระวัง", color: "#f6c90e" },
  { id: "NORMAL", th: "ปกติ", color: "#4caf7a" },
  { id: "NONE", th: "ไม่มีข้อมูล", color: "#c7cbd1" },
];

export const levelInfo = (level: Level) => LEVELS.find((l) => l.id === (level ?? "NONE"))!;

export interface Assessment {
  hazard: Hazard;
  level: Level;
  confidence: number;
  rule_ids: string[];
  notices: string[];
  observations: Record<string, number | null>;
  sources: string[];
  worst_area?: string;
}

export interface Dam {
  id: string | null;
  name_th: string;
  province_th: string | null;
  province_code: string | null;
  storage_percent: number | null; // % of normal storage level (รนก.), can exceed 100
  storage_mcm: number | null;
  usable_percent: number | null;
  inflow_mcm: number | null;
  released_mcm: number | null;
  spilled_mcm: number | null;
  inflow_acc_mcm: number | null; // since 1 January
  released_acc_mcm: number | null; // since 1 January
  inflow_avg_year_mcm: number | null; // long-term average yearly inflow
  normal_storage_mcm: number | null;
  max_storage_mcm: number | null;
  basin_th: string | null;
  agency_th: string | null;
  status: "HIGH" | "LOW_CRITICAL" | "NORMAL" | "UNKNOWN";
  status_th: string;
  date: string | null;
  lat: number | null;
  lon: number | null;
}

/** Badge colour for a dam, following ThaiWater's scale (>100 dark red, >80 red, low usable water yellow). */
export function damBadge(d: Dam): string {
  if (d.status === "HIGH") return (d.storage_percent ?? 0) > 100 ? "level-CRITICAL" : "level-WARNING";
  if (d.status === "LOW_CRITICAL") return "level-WATCH";
  return d.status === "NORMAL" ? "level-NORMAL" : "level-NONE";
}

/** 1 million m³ released in a day is about 11.6 m³ every second on average. */
export const mcmPerDayToM3s = (mcm: number) => (mcm * 1_000_000) / 86_400;

/** Map colour for a dam point (same meaning as damBadge). */
export function damColor(d: Dam): string {
  if (d.status === "HIGH") return (d.storage_percent ?? 0) > 100 ? "#ad2337" : "#e07b16";
  if (d.status === "LOW_CRITICAL") return "#d4a600";
  return d.status === "NORMAL" ? "#2f7fb5" : "#8a97a3";
}

export interface River {
  id: string | null;
  code: string;
  name_th: string | null;
  river_th: string | null;
  province_code: string | null;
  province_th: string | null;
  amphoe_th: string | null;
  basin_th: string | null;
  agency: string | null;
  lat: number;
  lon: number;
  time: string | null;
  level_msl: number | null;
  level_change_m: number | null; // vs the previous reading
  bank_msl: number | null;
  above_bank_m: number | null; // + over the bank, - below it
  discharge_m3s: number | null;
  qmax_m3s: number | null; // flow the channel carries without overflowing
  capacity_percent: number | null;
  status: "OVERFLOW" | "NEAR_BANK" | "NORMAL" | "UNKNOWN";
  status_th: string;
  is_key_station: boolean;
  chain_order: number | null; // position on the Chao Phraya main stem, upstream first
}

export const RIVER_STATUS: { id: River["status"]; th: string; color: string }[] = [
  { id: "OVERFLOW", th: "น้ำล้นตลิ่ง", color: "#d7263d" },
  { id: "NEAR_BANK", th: "น้ำใกล้ล้นตลิ่ง", color: "#f77f00" },
  { id: "NORMAL", th: "น้ำต่ำกว่าตลิ่ง", color: "#2f7fb5" },
  { id: "UNKNOWN", th: "ไม่ทราบระดับตลิ่ง", color: "#8a97a3" },
];
export const riverColor = (r: River) => RIVER_STATUS.find((s) => s.id === r.status)?.color ?? "#8a97a3";
export const AGENCY_TH: Record<string, string> = { RID: "กรมชลประทาน", EGAT: "กฟผ.", HII: "สสน.", DWR: "กรมทรัพยากรน้ำ" };

export interface StationGraph {
  points: { time: string; level_msl: number | null; discharge_m3s: number | null }[];
  bank_msl: number | null;
  ground_msl: number | null;
  qmax_m3s: number | null;
}

export interface DamHistory {
  series: { year: number; values: [string, number][] }[];
  upper_rule_curve: [string, number][]; // "MM-DD", million m³
  lower_rule_curve: [string, number][];
  normal_storage_mcm: number | null;
  max_storage_mcm: number | null;
  min_storage_mcm: number | null;
}

export interface MediaImage { image: string; thumb: string; time: string | null }
export interface Media {
  radar: (MediaImage & { id: string; name_th: string; agency_th: string })[];
  rain_forecast: Record<string, { label_th: string; days: (MediaImage & { date: string })[] }>;
  storm: (MediaImage & { source: string; source_th: string; region_th: string })[];
  wave: MediaImage[];
  data_updated_at: string | null;
}

export interface Earthquake {
  id: string | null;
  time: string;
  magnitude: number;
  lat: number;
  lon: number;
  depth_km: number | null;
  place_th: string | null; // TMD: country or province
  where_th: string | null; // TMD: direction and distance from a Thai district
  place: string | null; // USGS, English
  url: string | null;
  tsunami: boolean;
  source: string;
}

export interface RegionalEvent {
  id: string;
  type: string;
  type_th: string;
  name: string | null;
  alert: "Green" | "Orange" | "Red" | null;
  alert_th: string | null;
  countries: string | null;
  severity_text: string | null;
  from: string | null;
  to: string;
  current: boolean;
  lat: number;
  lon: number;
  url: string | null;
}

export interface AreaHazards {
  area: { code: string; level: string; name_th: string; name_en: string; parents: string[] };
  assessments: Assessment[];
  forecast: { date: string; rain_mm: number | null; level: Level; cond_th: string | null; wind_max_kmh: number | null; source: string }[];
  forecast_updated_at: string | null;
  dams: Dam[];
  rivers: River[];
  official_warnings: { title: string; headline?: string; url?: string; hazards: Hazard[] }[];
  data_updated_at: string | null;
  degraded_services: string[];
}

export type AreaBoundary = Omit<GeoJSON.Feature, "bbox"> & { bbox: [number, number, number, number] };

export interface AskAnswer {
  text: string;
  intent: string;
  area: { code: string; level: string; label: string; lat: number; lon: number } | null;
  sources: string[];
  llm_used?: boolean;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`/api${path}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json();
}

export const api = {
  layer: (hazard: Hazard) => get<GeoJSON.FeatureCollection & { meta: { data_updated_at: string; degraded_services: string[]; inputs_pending?: string[] } }>(`/v1/map/layers/${hazard}`),
  area: (code: string) => get<AreaHazards>(`/v1/areas/${code}/hazards`),
  dams: () => get<{ dams: Dam[]; data_updated_at: string | null }>(`/v1/dams`),
  damHistory: (id: string, series: "storage" | "released" | "inflow") => get<DamHistory>(`/v1/dams/${encodeURIComponent(id)}/history?series=${series}`),
  rivers: () => get<{ rivers: River[]; data_updated_at: string | null }>(`/v1/rivers`),
  riverGraph: (id: string, days = 3) => get<StationGraph>(`/v1/rivers/${encodeURIComponent(id)}/graph?days=${days}`),
  media: () => get<Media>(`/v1/media`),
  earthquakes: () => get<{ earthquakes: Earthquake[] }>(`/v1/earthquakes`),
  regionalEvents: () => get<{ events: RegionalEvent[] }>(`/v1/regional-events`),
  boundary: (code: string) => get<AreaBoundary>(`/v1/areas/${encodeURIComponent(code)}/boundary`),
  search: (q: string) => get<{ code: string; level: string; name_th: string; parents: string[]; lat: number; lon: number }[]>(`/v1/areas/search?q=${encodeURIComponent(q)}`),
  ask: async (question: string): Promise<AskAnswer> => {
    const res = await fetch("/api/v1/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question }),
    });
    if (!res.ok) throw new Error(`${res.status}`);
    return res.json();
  },
};

/** TMD/BMA heat-index band, shown next to the value (alert levels come from the backend rules). */
export function tmdHeatLabel(heatIndex: number): string {
  if (heatIndex >= 52) return "อันตรายมาก";
  if (heatIndex >= 42) return "อันตราย";
  if (heatIndex >= 33) return "เตือนภัย";
  return heatIndex >= 27 ? "เฝ้าระวัง" : "ปกติ";
}

export function describeObservation(key: string, value: number | null): string | null {
  if (value === null || value === undefined) return null;
  switch (key) {
    case "above_bank_m":
      return `ระดับน้ำ${value > 0 ? "สูงกว่า" : "ต่ำกว่า"}ตลิ่ง ${Math.abs(value).toFixed(2)} ม.`;
    case "satellite_flood_cells_7d":
      // H3 cells of ~0.1245 km² each (GISTDA Sentinel-1).
      // Spread of the flood (cells with some water), not the water area itself.
      return value > 0 ? `ดาวเทียมพบน้ำท่วมกระจายในพื้นที่ราว ${Math.round(value * 0.1245)} ตร.กม. (7 วัน)` : "ดาวเทียมไม่พบน้ำท่วมใน 7 วัน";
    case "rain_24h":
      return `ฝน 24 ชม. สูงสุด ${value.toFixed(1)} มม.`;
    case "hotspots_24h":
      return `จุดความร้อน 24 ชม. ${value} จุด`;
    case "fire_probability_7d":
      return `แบบจำลองประเมินโอกาสเกิดไฟใน 7 วัน ${Math.round(value * 100)}%`;
    case "vci":
      // Vegetation Condition Index: 0 = browner than any past year for this month, 100 = greener than any.
      return `ดัชนีความสมบูรณ์ของพืช (VCI) ${value.toFixed(0)} จาก 100`;
    case "ndvi_deficit":
      return value < 0.05 ? "พืชพรรณเขียวใกล้เคียงปกติของเดือนนี้ (NDVI)" : `NDVI ต่ำกว่าปกติ ${value.toFixed(2)}`;
    case "pm25_24h":
      return `PM2.5 เฉลี่ย 24 ชม. ${value.toFixed(1)} มคก./ลบ.ม. (สถานีกรมควบคุมมลพิษ)`;
    case "pm25_model_24h":
      return `แบบจำลองคาดการณ์ PM2.5 เฉลี่ย 24 ชม. ข้างหน้า ${value.toFixed(1)} มคก./ลบ.ม.`;
    case "heat_index_max_24h":
      return `ดัชนีความร้อนสูงสุดใน 24 ชม. ข้างหน้า ${value.toFixed(1)}°C (เกณฑ์กรมอุตุฯ: ${tmdHeatLabel(value)})`;
    case "quake_mmi_24h":
      return value <= 1 ? "ไม่มีแผ่นดินไหวที่รู้สึกได้ใน 24 ชม." : `ประเมินแรงสั่นระดับ ${value.toFixed(0)} (มาตราเมอร์คัลลี)`;
    case "quake_magnitude":
      return `แผ่นดินไหวขนาด ${value.toFixed(1)}`;
    case "quake_distance_km":
      return `ห่างราว ${value.toFixed(0)} กม.`;
    case "cross_border_hotspots_24h":
      return value > 0 ? `จุดความร้อนในประเทศเพื่อนบ้านในรัศมี 100 กม. ${value} จุด (24 ชม.)` : null;
    case "spi_3":
      return `SPI-3 ${value.toFixed(2)}`;
    default:
      return `${key}: ${value}`;
  }
}
