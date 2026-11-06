"use client";

import { type AreaHazards, type Level, levelInfo } from "@/lib/api";
import { getLocale, t as tr } from "@/lib/i18n";
import Icon from "@/components/Icon";

export type Metric = "air_quality" | "heat" | "rain_storm";
export const ENVIRONMENT_METRICS: readonly Metric[] = ["air_quality", "heat", "rain_storm"];
export type EnvironmentLayer = GeoJSON.FeatureCollection & { meta: { data_updated_at: string; degraded_services: string[]; inputs_pending?: string[] } };
export type EnvironmentState = { key: string; data: EnvironmentLayer | null; error: boolean };
// The heat card shows the forecast for the hour it is now; its level and the small line use the 24-h peak.
// The rain card shows the highest chance of rain in the next 24 hours and the Thai hour of that peak (display only:
// rules.yaml does not read it).
const fields = { air_quality: "pm25_24h", heat: "heat_index_now", rain_storm: "rain_chance_max_24h" } as const;
const HEAT_PEAK = "heat_index_max_24h";
const RAIN_PEAK_HOUR = "rain_chance_peak_hour";
// The backend's name for each card's Open-Meteo forecast while it is still loading after a restart (`inputs_pending`).
// The chance of rain comes in the same Open-Meteo call as the heat forecast.
const models = { air_quality: "air_model", heat: "heat_model", rain_storm: "heat_model" } as const;
const titles = { air_quality: "ฝุ่น PM2.5", heat: "ดัชนีความร้อน", rain_storm: "โอกาสฝนตก" } as const;
const units = { air_quality: " มคก./ลบ.ม.", heat: "°C", rain_storm: "%" } as const;

export function highestEnvironmentValue(layer: GeoJSON.FeatureCollection | null, metric: Metric) {
    let highest: { value: number; peak: number | null; hour: number | null; name: string; province: string; level: Level } | null = null;
    for (const feature of layer?.features ?? []) {
        const p = feature.properties;
        const value = p?.[fields[metric]];
        if (typeof value !== "number" || !Number.isFinite(value)) continue;
        if (!highest || value > highest.value)
            highest = { value, peak: metric === "heat" ? p?.[HEAT_PEAK] ?? null : null, hour: metric === "rain_storm" ? p?.[RAIN_PEAK_HOUR] ?? null : null,
                        name: p?.name_th ?? "", province: p?.province_th ?? "", level: p?.level ?? null };
    }
    return highest;
}

function updatedTime(value: string | null | undefined) {
    if (!value || Number.isNaN(Date.parse(value))) return tr("ยังไม่มีเวลาข้อมูล");
    return new Date(value).toLocaleString(getLocale(), { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Bangkok" });
}

export default function EnvironmentSummary({ layers, area, loadingArea, areaError = false }: {
    layers: Record<Metric, EnvironmentState | null>;
    area: AreaHazards | null;
    loadingArea: boolean;
    areaError?: boolean;
}) {
    return <section className="environment-summary" aria-label={tr("ฝุ่น ความร้อน และโอกาสฝน")}>
        {ENVIRONMENT_METRICS.map(metric => {
            const state = layers[metric];
            const assessment = area?.assessments.find(a => a.hazard === metric);
            const maximum = !area ? highestEnvironmentValue(state?.data ?? null, metric) : null;
            const measured = assessment?.observations[fields[metric]];
            const model = metric === "air_quality" && area && measured == null;
            const value = area ? (model ? assessment?.observations.pm25_model_24h : measured) : maximum?.value;
            const hasValue = typeof value === "number" && Number.isFinite(value);
            const peak = metric === "heat" ? (area ? assessment?.observations[HEAT_PEAK] : maximum?.peak) : null;
            const hour = metric === "rain_storm" ? (area ? assessment?.observations[RAIN_PEAK_HOUR] : maximum?.hour) : null;
            const peakHour = typeof hour === "number" && Number.isFinite(hour) ? "ราว " + String(hour).padStart(2, "0") + ":00 น." : "";
            // Only this card's own forecast counts; another input still loading does not make this card incomplete.
            const pending = !area && !!state?.data?.meta.inputs_pending?.includes(models[metric]);
            const loading = loadingArea || (!area && !areaError && !state?.data && !state?.error) || (pending && !hasValue);
            const error = areaError || (!area && state?.error);
            const available = !loading && !error && hasValue;
            const level = available ? (area ? assessment?.level : maximum?.level) ?? null : null;
            const source = model ? "Open-Meteo / CAMS (แบบจำลอง)" : metric === "air_quality" ? "Air4Thai / กรมควบคุมมลพิษ" : metric === "rain_storm" ? "Open-Meteo (แบบจำลองโอกาสฝน)" : "Open-Meteo";
            const degraded = area ? area.degraded_services.length : state?.data?.meta.degraded_services.length;
            const status = loading ? "กำลังโหลด…" : error ? "โหลดข้อมูลไม่ได้" : !available ? "ยังไม่มีข้อมูล" : levelInfo(level).th;
            const scope = area ? area.area.name_th : "สูงสุดในประเทศ · อำเภอที่มีข้อมูล";
            const place = available && !area ? [maximum?.name, maximum?.province].filter(Boolean).join(" · ") : "";
            const period = metric === "heat" ? "ค่าคาดการณ์ชั่วโมงนี้" : metric === "rain_storm" ? ["สูงสุดใน 24 ชม.", peakHour].filter(Boolean).join(" ") : model ? "24 ชม. ข้างหน้า" : "เฉลี่ย 24 ชม.";
            const peakDetail = !loading && !error && typeof peak === "number" && Number.isFinite(peak) ? tr(" · สูงสุดใน 24 ชม. ข้างหน้า ") + peak.toLocaleString(getLocale(), { maximumFractionDigits: 1 }) + "°C" : "";
            // A card with nothing to show is left out rather than shown as "—", which read as a broken page.
            if (!loading && !error && !available) return null;
            const details = [scope, place, period, peakDetail, source, updatedTime(area ? area.data_updated_at : state?.data?.meta.data_updated_at), pending ? "ข้อมูลยังไม่ครบ" : degraded ? "ข้อมูลบางแหล่งไม่พร้อมใช้งาน" : ""].filter(Boolean).map(text => tr(text)).join(" · ");
            return <article key={metric} title={details} aria-busy={loading || undefined}>
                <span className="environment-icon" aria-hidden="true"><Icon name={metric}/></span>
                <h3>{tr(titles[metric])}</h3>
                <p className="environment-value">{metric === "heat" && available && <span className="sr-only">{tr("ตอนนี้ร้อนเหมือน ")}</span>}<strong>{available ? value.toLocaleString(getLocale(), { maximumFractionDigits: metric === "rain_storm" ? 0 : 1 }) : <span className="environment-shimmer" aria-hidden="true"/>}</strong>{available && <span>{tr(units[metric])}</span>}{available && peakHour && <span className="environment-hour">{tr(peakHour)}</span>}</p>
                <small className="environment-status">{tr(available ? (model || metric === "rain_storm" ? "แบบจำลอง · " : "") + period : status)}</small>
                {/* The rain card's level is the rain/storm level, set by the amount of rain, not by the chance shown. */}
                <span className="sr-only"><span className={"risk-badge level-" + (level ?? "NONE")}>{metric === "rain_storm" && available && tr("ระดับภัยฝนและพายุ (จากปริมาณฝน): ")}{tr(status)}</span> · {details}</span>
            </article>;
        })}
    </section>;
}
