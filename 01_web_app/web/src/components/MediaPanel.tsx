"use client";

import { t as tr, getLocale } from "@/lib/i18n";
import { useEffect, useState } from "react";
import { api, Media, MediaImage } from "@/lib/api";
import Icon from "@/components/Icon";
type Tab = "radar" | "forecast" | "storm" | "wave";
const TABS: {
    id: Tab;
    th: string;
}[] = [
    { id: "radar", th: "เรดาร์ฝน" },
    { id: "forecast", th: "คาดการณ์ฝน" },
    { id: "storm", th: "พายุ" },
    { id: "wave", th: "คลื่นทะเล" },
];
function when(iso: string | null, withTime = true) {
    if (!iso)
        return "";
    return new Date(iso).toLocaleString(getLocale(), { weekday: withTime ? undefined : "short", day: "numeric", month: "short", ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}), timeZone: "Asia/Bangkok" });
}
function Picture({ item, alt, caption }: {
    item: MediaImage;
    alt: string;
    caption: string;
}) {
    return (<figure className="media-figure">
      {/* Images are served by ThaiWater; next/image would proxy and re-encode every radar frame. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <a href={item.image} target="_blank" rel="noreferrer noopener"><img src={item.image} alt={tr(alt)} loading="lazy" referrerPolicy="no-referrer"/></a>
      <figcaption>{tr(caption)} · <a href={item.image} target="_blank" rel="noreferrer noopener">{tr("เปิดภาพเต็ม")}</a></figcaption>
    </figure>);
}
function Steps<T>({ items, index, onChange, label }: {
    items: T[];
    index: number;
    onChange: (i: number) => void;
    label: (t: T) => string;
}) {
    return (<div className="media-steps" role="group">
      {tr(items.map((t, i) => <button key={i} type="button" className={i === index ? "is-active" : ""} aria-pressed={i === index} onClick={() => onChange(i)}>{tr(label(t))}</button>))}
    </div>);
}
export default function MediaPanel({ revision }: {
    revision: number;
}) {
    const [media, setMedia] = useState<{
        data: Media | null;
        error: boolean;
    } | null>(null);
    const [tab, setTab] = useState<Tab>("radar");
    const [radarId, setRadarId] = useState<string | null>(null);
    const [region, setRegion] = useState("thailand");
    const [day, setDay] = useState(0);
    const [wave, setWave] = useState(0);
    useEffect(() => {
        let cancelled = false;
        api.media()
            .then((data) => !cancelled && setMedia({ data, error: false }))
            .catch(() => !cancelled && setMedia((old) => ({ data: old?.data ?? null, error: true })));
        return () => { cancelled = true; };
    }, [revision]);
    const data = media?.data;
    // Default radar: Suvarnabhumi (covers Bangkok), else the first fresh one.
    const radar = data?.radar.find((r) => r.id === radarId) ?? data?.radar.find((r) => r.id === "svp120") ?? data?.radar[0];
    const forecast = data?.rain_forecast[region];
    const forecastDay = forecast?.days[Math.min(day, (forecast?.days.length ?? 1) - 1)];
    return (<section className="media-panel" aria-label={tr("ภาพเรดาร์และแผนที่คาดการณ์")}>
      <div className="legend-heading"><h3><Icon name="radar"/>{tr("ภาพเรดาร์และแผนที่คาดการณ์")}</h3></div>
      <div className="dam-sort media-tabs" role="tablist">
        {tr(TABS.map((t) => <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} className={tab === t.id ? "is-active" : ""} onClick={() => setTab(t.id)}>{tr(t.th)}</button>))}
      </div>
      {tr(!media && <p className="search-feedback" role="status">{tr("กำลังโหลดภาพ\u2026")}</p>)}
      {tr(media?.error && !data && <p className="search-feedback data-notice" role="alert">{tr("โหลดภาพไม่สำเร็จ")}</p>)}
      {tr(data && tab === "radar" && (data.radar.length && radar ? <>
        <label className="sr-only" htmlFor="radar-select">{tr("เลือกเรดาร์")}</label>
        <select id="radar-select" className="media-select" value={radar.id} onChange={(e) => setRadarId(e.target.value)}>
          {tr(data.radar.map((r) => <option key={r.id} value={r.id}>{tr(r.name_th)}</option>))}
        </select>
        <Picture item={radar} alt={tr(tr("ภาพเรดาร์ฝน") + tr(radar.name_th) + tr(""))} caption={tr("") + tr(radar.agency_th) + tr(" \u00B7 ") + tr(when(radar.time)) + tr(" น.")}/>
        <p className="source-note">{tr("สีเขียว-เหลือง = ฝนเล็กน้อยถึงปานกลาง \u00B7 สีแดง-ม่วง = ฝนหนักมาก \u00B7 แสดงเฉพาะเรดาร์ที่อัปเดตภายใน 3 ชั่วโมง (")}{tr(data.radar.length)}{tr(" สถานี)")}</p>
      </> : <p className="search-feedback">{tr("ตอนนี้ไม่มีภาพเรดาร์ที่อัปเดตภายใน 3 ชั่วโมง")}</p>))}
      {tr(data && tab === "forecast" && <>
        <div className="media-steps" role="group" aria-label={tr("เลือกพื้นที่")}>
          {tr(Object.entries(data.rain_forecast).filter(([, v]) => v.days.length).map(([k, v]) => (<button key={k} type="button" className={region === k ? "is-active" : ""} aria-pressed={region === k} onClick={() => { setRegion(k); setDay(0); }}>{tr(v.label_th)}</button>)))}
        </div>
        {tr(forecast && forecastDay ? <>
          <Steps items={forecast.days} index={Math.min(day, forecast.days.length - 1)} onChange={setDay} label={(d) => when(d.time, false)}/>
          <Picture item={forecastDay} alt={tr(tr("แผนที่คาดการณ์ฝน") + tr(forecast.label_th) + tr(" ") + tr(when(forecastDay.time, false)) + tr(""))} caption={tr("คาดการณ์ฝนสะสมรายวัน ") + tr(when(forecastDay.time, false)) + tr("")}/>
          <p className="source-note">{tr("แบบจำลอง WRF-ROMS ของ สสน. \u00B7 เป็นการคาดการณ์ ไม่ใช่ประกาศเตือนภัย")}</p>
        </> : <p className="search-feedback">{tr("ยังไม่มีแผนที่คาดการณ์ฝนรอบล่าสุด")}</p>)}
      </>)}
      {tr(data && tab === "storm" && (data.storm.length ? <div className="media-grid">
        {tr(data.storm.map((s) => <Picture key={s.image} item={s} alt={tr(`${s.region_th} ${s.source_th}`)} caption={tr("") + tr(s.region_th) + tr(" \u00B7 ") + tr(s.source_th) + tr(" \u00B7 ") + tr(when(s.time)) + tr(" น.")}/>))}
      </div> : <p className="search-feedback">{tr("ยังไม่มีแผนที่ติดตามพายุที่อัปเดตใน 2 วัน")}</p>))}
      {tr(data && tab === "wave" && (data.wave.length ? <>
        <Steps items={data.wave} index={Math.min(wave, data.wave.length - 1)} onChange={setWave} label={(w) => when(w.time, false)}/>
        <Picture item={data.wave[Math.min(wave, data.wave.length - 1)]} alt={tr("แผนที่คาดการณ์ความสูงคลื่น")} caption={tr("คาดการณ์ความสูงคลื่น ") + tr(when(data.wave[Math.min(wave, data.wave.length - 1)].time)) + tr(" น.")}/>
        <p className="source-note">{tr("แบบจำลอง SWAN ของ สสน. \u00B7 ใช้ประกอบการตัดสินใจออกเรือ ร่วมกับประกาศกรมอุตุนิยมวิทยา")}</p>
      </> : <p className="search-feedback">{tr("ยังไม่มีแผนที่คาดการณ์คลื่นรอบล่าสุด")}</p>))}
      <p className="source-note">{tr("ภาพจาก ThaiWater (สสน.) และหน่วยงานเจ้าของข้อมูล \u00B7 แตะภาพเพื่อดูขนาดเต็ม")}</p>
    </section>);
}
