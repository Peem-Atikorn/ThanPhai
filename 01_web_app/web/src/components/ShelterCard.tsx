"use client";

import { useEffect, useRef, useState } from "react";
import Icon from "@/components/Icon";
import { api, type AreaHazards, type RouteProfile, type Shelter, type ShelterRoute } from "@/lib/api";
import { getLocale, t as tr } from "@/lib/i18n";

// DDPM shelters for the open area, and the nearest ones to the visitor. Only places from DDPM's list are shown:
// nothing here suggests a place of its own, and every list says to check before going.
const SHOWN = 3;
const CHECK = "ก่อนเดินทาง โปรดสอบถาม อปท. ในพื้นที่ หรือโทรสายด่วน ปภ. 1784 ว่าศูนย์เปิดอยู่";
const number = () => new Intl.NumberFormat(getLocale());
// Directions to the shelter only: the visitor's own position is not put in the link.
const directions = (s: Shelter) => `https://www.google.com/maps/dir/?api=1&destination=${s.lat},${s.lon}`;

function ShelterItem({ shelter, distance, onRoute, routing }: { shelter: Shelter; distance?: number; onRoute?: () => void; routing?: boolean }) {
    const where = [shelter.tambon && tr("ต.") + shelter.tambon, shelter.amphoe && tr("อ.") + shelter.amphoe].filter(Boolean).join(" ");
    return <li>
        <span><strong>{shelter.name}</strong><small>{tr(where)}{tr(shelter.capacity ? tr(" · รองรับ ") + number().format(shelter.capacity) + tr(" คน") : "")}{tr(distance != null ? tr(" · ห่าง ") + distance.toFixed(1) + tr(" กม.") : "")}{tr(shelter.approximate ? tr(" · ตำแหน่งโดยประมาณ") : "")}</small></span>
        <span className="shelter-actions">
            {onRoute && <button type="button" className="text-button" onClick={onRoute} disabled={routing}>{tr(routing ? "กำลังคำนวณ…" : "เส้นทาง")}</button>}
            <a href={directions(shelter)} target="_blank" rel="noreferrer noopener">{tr("Google Maps")}</a>
        </span>
    </li>;
}

/** Shelters of the selected province or district; `urgent` (a warning for water or storm) puts the card forward. */
export function ShelterCard({ area, urgent, onShowMap }: { area: AreaHazards["area"]; urgent: boolean; onShowMap: () => void }) {
    const [loaded, setLoaded] = useState<{ code: string; list: Shelter[] | null }>({ code: "", list: null });
    const bangkok = area.code.startsWith("TH10");
    useEffect(() => {
        if (bangkok)
            return;
        let cancelled = false;
        api.shelters(area.code)
            .then((d) => { if (!cancelled) setLoaded({ code: area.code, list: d.shelters }); })
            .catch(() => { if (!cancelled) setLoaded({ code: area.code, list: null }); });
        return () => { cancelled = true; };
    }, [area.code, bangkok]);
    const scope = area.level === "province" ? "ในจังหวัดนี้" : area.level === "tambon" ? "ในอำเภอของตำบลนี้" : "ในอำเภอนี้";
    if (bangkok)
        return <section className="shelter-card"><h3><Icon name="shelter"/>{tr("ศูนย์พักพิง")}</h3><p>{tr("รายชื่อของ ปภ. ยังไม่มีกรุงเทพฯ ติดตามศูนย์พักพิงของแต่ละเขตจากสำนักงานเขต หรือโทรสายด่วน กทม. 1555")}</p></section>;
    if (loaded.code !== area.code)
        return <section className="shelter-card" aria-busy="true"><h3><Icon name="shelter"/>{tr("ศูนย์พักพิง")}</h3><p>{tr("กำลังโหลด…")}</p></section>;
    if (!loaded.list)
        return <section className="shelter-card"><h3><Icon name="shelter"/>{tr("ศูนย์พักพิง")}</h3><p>{tr("โหลดรายชื่อศูนย์พักพิงไม่สำเร็จ")}</p></section>;
    const list = loaded.list;
    const capacity = list.reduce((sum, s) => sum + (s.capacity ?? 0), 0);
    const biggest = [...list].sort((a, b) => (b.capacity ?? 0) - (a.capacity ?? 0)).slice(0, SHOWN);
    return <section className={"shelter-card" + (urgent ? " is-urgent" : "")}>
        <h3><Icon name="shelter"/>{tr("ศูนย์พักพิง")}{tr(scope)}</h3>
        {list.length === 0 ? <p>{tr("รายชื่อของ ปภ. ไม่มีศูนย์พักพิงในพื้นที่นี้")}</p> : <>
            <p className="shelter-total"><strong>{tr(number().format(list.length))}{tr(" แห่ง")}</strong>{tr(capacity ? tr(" · รองรับรวม ") + number().format(capacity) + tr(" คน") : "")}</p>
            <ul className="shelter-list">{biggest.map((s) => <ShelterItem key={`${s.name}-${s.lat}-${s.lon}`} shelter={s}/>)}</ul>
            <button type="button" className="text-button" onClick={onShowMap}>{tr(list.length > SHOWN ? tr("ดูทั้ง ") + number().format(list.length) + tr(" แห่งบนแผนที่") : "ดูบนแผนที่")}<Icon name="arrow"/></button>
        </>}
        <small>{tr(CHECK)}</small>
    </section>;
}

const FLOOD_NOTE: Record<ShelterRoute["flood"]["status"], string> = {
    avoided: "วางเส้นทางเลี่ยงพื้นที่ที่ดาวเทียมพบน้ำท่วมในช่วง 7 วันแล้ว",
    clear: "ดาวเทียมไม่พบน้ำท่วมบนเส้นทางนี้ในช่วง 7 วัน",
    crosses: "เส้นทางผ่านพื้นที่ที่ดาวเทียมพบน้ำท่วม",
    unchecked: "ยังตรวจพื้นที่น้ำท่วมจากดาวเทียมไม่ได้",
};

function RouteSummary({ route }: { route: ShelterRoute }) {
    const minutes = Math.max(1, Math.round(route.duration_s / 60));
    const flood = route.flood;
    return <div className={"route-summary" + (flood.status === "crosses" ? " is-warning" : "")} role="status">
        <strong>{route.shelter.name}</strong>
        <p>{tr(number().format(Math.round(route.distance_m / 100) / 10))}{tr(" กม. · ประมาณ ")}{tr(minutes)}{tr(route.profile === "foot-walking" ? " นาที (เดิน)" : " นาที (รถยนต์)")}</p>
        <p>{tr(flood.status === "crosses" ? "⚠ " : "")}{tr(FLOOD_NOTE[flood.status])}{tr(flood.status === "crosses" ? tr(" ราว ") + flood.crossing_km.toFixed(1) + tr(" กม. ลองศูนย์อื่น หรือโทร 1784") : "")}{tr(flood.partial ? " (ข้อมูลน้ำท่วมบางส่วน)" : "")}</p>
        {flood.area && <small><span className="legend-dot" style={{ background: "#38bdf8", display: "inline-block", marginRight: 4 }}/>{tr("สีฟ้าบนแผนที่ = น้ำท่วมที่ดาวเทียมพบในช่วง 7 วัน รอบเส้นทางนี้")}</small>}
        <small>{tr("เส้นทางโดยประมาณ ไม่ได้ตรวจสภาพถนนจริง ข้อมูลดาวเทียมอาจช้ากว่าสถานการณ์จริง ฟังประกาศของ ปภ. และเจ้าหน้าที่ในพื้นที่")}</small>
        <small>{tr(route.attribution)}{tr(" · ")}{tr(flood.source)}</small>
    </div>;
}

/** "Find the nearest": the browser's position goes to ThanPhai's API only, and is not kept. */
export function NearestShelters({ onRoute }: { onRoute?: (route: ShelterRoute | null) => void }) {
    const [state, setState] = useState<{ status: "idle" | "locating" | "ok" | "error"; list: (Shelter & { distance_km: number })[]; error?: string }>({ status: "idle", list: [] });
    const [profile, setProfile] = useState<RouteProfile>("driving-car");
    const [route, setRoute] = useState<{ status: "idle" | "loading" | "ok" | "error"; key?: string; data?: ShelterRoute; error?: string }>({ status: "idle" });
    const request = useRef(0);
    // Kept in memory for this page only, so a route can start from it.
    const position = useRef<{ lat: number; lon: number } | null>(null);
    function find() {
        if (!navigator.geolocation || !window.isSecureContext) {
            setState({ status: "error", list: [], error: "การใช้ตำแหน่งต้องเปิดเว็บผ่าน HTTPS หรือ localhost และใช้เบราว์เซอร์ที่รองรับ GPS" });
            return;
        }
        const id = ++request.current;
        setState({ status: "locating", list: [] });
        setRoute({ status: "idle" });
        onRoute?.(null);
        navigator.geolocation.getCurrentPosition(async (pos) => {
            position.current = { lat: pos.coords.latitude, lon: pos.coords.longitude };
            try {
                const d = await api.nearestShelters(pos.coords.latitude, pos.coords.longitude);
                if (id === request.current) setState({ status: "ok", list: d.shelters });
            }
            catch (error) {
                if (id === request.current) setState({ status: "error", list: [], error: error instanceof Error && error.message.startsWith("422") ? "ตำแหน่งนี้อยู่นอกประเทศไทย" : "ยังค้นหาศูนย์พักพิงไม่ได้ กรุณาลองใหม่" });
            }
        }, (error) => {
            if (id === request.current) setState({ status: "error", list: [], error: error.code === 1 ? "กรุณาอนุญาตการเข้าถึงตำแหน่ง หรือค้นหาด้วยชื่อพื้นที่แทน" : "ยังรับตำแหน่ง GPS ไม่ได้ ลองเปิดบริการตำแหน่งหรือค้นหาด้วยชื่อพื้นที่" });
        }, { enableHighAccuracy: false, timeout: 15000, maximumAge: 300000 });
    }
    async function showRoute(shelter: Shelter, mode: RouteProfile = profile) {
        if (!position.current)
            return;
        const key = `${shelter.lat},${shelter.lon},${mode}`;
        setRoute({ status: "loading", key });
        try {
            const data = await api.shelterRoute(position.current, shelter, mode);
            setRoute((current) => current.key === key ? { status: "ok", key, data } : current);
            onRoute?.(data);
        }
        catch (error) {
            const code = error instanceof Error ? error.message : "";
            setRoute((current) => current.key === key ? { status: "error", key, error: code.startsWith("429") ? "ขอเส้นทางถี่เกินไป กรุณารอสักครู่" : code.startsWith("422") ? "ศูนย์นี้อยู่ไกลเกิน 50 กม. โปรดโทร 1784" : code.startsWith("503") ? "ยังไม่ได้เปิดใช้การคำนวณเส้นทาง" : "ยังคำนวณเส้นทางไม่ได้ ใช้ปุ่มนำทางของ Google Maps แทนได้" } : current);
        }
    }
    function changeProfile(mode: RouteProfile) {
        setProfile(mode);
        if (route.data) void showRoute(route.data.shelter, mode);
    }
    return <div className="nearest-shelters">
        <button type="button" className="secondary-button" onClick={find} disabled={state.status === "locating"}><Icon name="pin"/>{tr(state.status === "locating" ? "กำลังหาตำแหน่ง…" : "หาศูนย์ใกล้ฉัน")}</button>
        {state.status === "error" && <p className="data-notice">{tr(state.error)}</p>}
        {state.status === "ok" && <>
            <div className="route-profile" role="group" aria-label={tr("วิธีเดินทาง")}>
                {(["driving-car", "foot-walking"] as const).map((mode) => <button key={mode} type="button" aria-pressed={profile === mode} onClick={() => changeProfile(mode)}>{tr(mode === "driving-car" ? "รถยนต์" : "เดิน")}</button>)}
            </div>
            <ul className="shelter-list">{state.list.map((s) => <ShelterItem key={`${s.name}-${s.lat}-${s.lon}`} shelter={s} distance={s.distance_km}
                onRoute={() => showRoute(s)} routing={route.status === "loading" && route.key?.startsWith(`${s.lat},${s.lon},`)}/>)}</ul>
            <small>{tr("ระยะเป็นเส้นตรง ระยะทางจริงตามถนนอาจไกลกว่านี้ · ตำแหน่งของคุณใช้หาศูนย์และเส้นทางเท่านั้น ไม่ถูกเก็บ")}</small>
            {route.status === "error" && <p className="data-notice">{tr(route.error)}</p>}
            {route.status === "ok" && route.data && <><RouteSummary route={route.data}/><button type="button" className="text-button" onClick={() => { setRoute({ status: "idle" }); onRoute?.(null); }}>{tr("ซ่อนเส้นทาง")}</button></>}
        </>}
    </div>;
}
