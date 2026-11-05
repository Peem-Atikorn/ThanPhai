"use client";

import { t as tr, getLocale } from "@/lib/i18n";
import { useEffect, useState } from "react";
import Icon, { type IconName } from "@/components/Icon";
import { api, LEVELS, type MapOverlay, type OverlayId } from "@/lib/api";
export const DEFAULT_OVERLAYS = { flood: false, cyclones: false, waves: false };
const NAMES = { flood: "น้ำท่วมจากดาวเทียม", cyclones: "พายุหมุนเขตร้อน", waves: "คลื่นทะเล" };
const ICONS: Record<OverlayId, IconName> = { flood: "flood", cyclones: "tropical_cyclone", waves: "marine" };
const SOURCES = { flood: "gistda_flood", cyclones: "gdacs_cyclones", waves: "open_meteo_waves" };
const IDS = Object.keys(NAMES) as OverlayId[];
type Data = Partial<Record<OverlayId, MapOverlay>>;
type Enabled = Partial<Record<OverlayId, boolean>>;
const time = (value: string | null) => value ? new Date(value).toLocaleString(getLocale(), { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Bangkok" }) : "ยังไม่มีข้อมูล";
const emptyStorm = (id: OverlayId, data?: MapOverlay) => id === "cyclones" && data?.overlay.status === "ok" && data.features.length === 0;
export function useOverviewLayers(active: boolean, revision: number) {
    const [data, setData] = useState<Data>({});
    const [enabled, setEnabled] = useState(DEFAULT_OVERLAYS);
    useEffect(() => {
        if (!active)
            return;
        let cancelled = false;
        const timers: ReturnType<typeof setTimeout>[] = [];
        async function refresh(id: OverlayId) {
            try {
                const result = await api.overlay(id);
                if (cancelled)
                    return;
                setData(old => ({ ...old, [id]: result }));
                if (result.overlay.status === "pending")
                    timers.push(setTimeout(() => refresh(id), 60000));
            }
            catch {
                if (cancelled)
                    return;
                setData(old => ({ ...old, [id]: old[id]?.overlay.status === "ok"
                        ? { ...old[id]!, degraded_services: [SOURCES[id]] }
                        : { type: "FeatureCollection", features: [], overlay: { status: "error", source: NAMES[id], updated_at: null }, degraded_services: [] } }));
            }
        }
        IDS.forEach(id => { void refresh(id); });
        return () => { cancelled = true; timers.forEach(clearTimeout); };
    }, [active, revision]);
    return { data, enabled, toggle: (id: OverlayId) => setEnabled(old => ({ ...old, [id]: !old[id] })) };
}
export function OverlayControls({ data, enabled, onToggle }: {
    data: Data;
    enabled: Enabled;
    onToggle: (id: OverlayId) => void;
}) {
    return <>{tr(IDS.map(id => {
            const status = data[id]?.overlay.status ?? "pending";
            return <button key={id} type="button" data-layer={id} aria-pressed={!!enabled[id]} disabled={status === "unavailable"} onClick={() => onToggle(id)}>
      <Icon name={ICONS[id]}/><span>{tr(NAMES[id])}<small>{tr(status === "pending" ? "กำลังโหลด…" : status === "unavailable" ? "ไม่พร้อมใช้งาน · ไม่มี key ของแหล่งข้อมูล" : status === "error" ? "โหลดไม่สำเร็จ · จะลองใหม่" : enabled[id] ? (emptyStorm(id, data[id]) ? "เปิด · ไม่พบพายุ" : "เปิด") : "ปิด")}</small></span><span className="layer-toggle" aria-hidden="true"/>
    </button>;
        }))}</>;
}
export function OverlayLegends({ data, enabled }: {
    data: Data;
    enabled: Enabled;
}) {
    return <div className="overlay-legends" aria-live="polite">{tr(IDS.filter(id => enabled[id] && !emptyStorm(id, data[id])).map(id => {
            const item = data[id];
            const status = item?.overlay.status ?? "pending";
            return <section key={id} aria-label={tr("คำอธิบายชั้น" + NAMES[id])}>
      <h4>{tr(NAMES[id])}</h4>
      {tr(status === "ok" ? <>
        {tr(id === "flood" ? <p><span className="overlay-flood-swatch"/>{tr("สีฟ้าเข้มขึ้นตามสัดส่วนน้ำท่วมใน 7 วัน \u00B7 แสดงขอบเขตอำเภอ ไม่ใช่ขอบน้ำจริง")}{tr(!item?.features.length && " · ไม่พบน้ำท่วมในข้อมูลชุดนี้")}</p>
                        : id === "cyclones" ? <p>{tr("พื้นที่สี = ลมแรง \u00B7 เส้นทึบ = เส้นทางที่ผ่านมา \u00B7 เส้นประ = คาดการณ์ \u00B7 จุดใหญ่ = ตำแหน่งล่าสุด")}</p>
                            : <p>{tr("ลูกศรชี้ทิศที่คลื่นวิ่งไป \u00B7 ขนาดตามความสูงคลื่นสูงสุดใน 24 ชม. ข้างหน้า")}</p>)}
        {tr(id !== "flood" && <ul className="overlay-levels">{tr(LEVELS.filter(l => l.id !== "NONE").map(l => <li key={l.id}><span className="legend-dot" style={{ background: l.color }}/>{tr(l.th)}</li>))}</ul>)}
        {tr(item?.degraded_services?.includes(SOURCES[id]) && <p className="data-notice">{tr("ข้อมูลเดิม \u00B7 แหล่งข้อมูลยังอัปเดตไม่สำเร็จ")}</p>)}
      </> : <p>{tr(status === "pending" ? "กำลังโหลด…" : status === "unavailable" ? "ไม่พร้อมใช้งาน · ไม่มี key ของแหล่งข้อมูล" : "โหลดไม่สำเร็จ · กดโหลดข้อมูลแผนที่ใหม่เพื่อลองอีกครั้ง")}</p>)}
      <small>{tr(item?.overlay.source ?? NAMES[id])} · {tr(time(item?.overlay.updated_at ?? null))}{tr(" (เวลาไทย)")}</small>
    </section>;
        }))}</div>;
}
