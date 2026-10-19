"use client";

import { useEffect, useState } from "react";
import { api, Media, MediaImage } from "@/lib/api";
import Icon from "@/components/Icon";

type Tab = "radar" | "forecast" | "storm" | "wave";
const TABS: { id: Tab; th: string }[] = [
  { id: "radar", th: "เรดาร์ฝน" },
  { id: "forecast", th: "คาดการณ์ฝน" },
  { id: "storm", th: "พายุ" },
  { id: "wave", th: "คลื่นทะเล" },
];

function when(iso: string | null, withTime = true) {
  if (!iso) return "";
  return new Date(iso).toLocaleString("th-TH", { weekday: withTime ? undefined : "short", day: "numeric", month: "short", ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}), timeZone: "Asia/Bangkok" });
}

function Picture({ item, alt, caption }: { item: MediaImage; alt: string; caption: string }) {
  return (
    <figure className="media-figure">
      {/* Images are served by ThaiWater; next/image would proxy and re-encode every radar frame. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <a href={item.image} target="_blank" rel="noreferrer noopener"><img src={item.image} alt={alt} loading="lazy" referrerPolicy="no-referrer" /></a>
      <figcaption>{caption} · <a href={item.image} target="_blank" rel="noreferrer noopener">เปิดภาพเต็ม</a></figcaption>
    </figure>
  );
}

function Steps<T>({ items, index, onChange, label }: { items: T[]; index: number; onChange: (i: number) => void; label: (t: T) => string }) {
  return (
    <div className="media-steps" role="group">
      {items.map((t, i) => <button key={i} type="button" className={i === index ? "is-active" : ""} aria-pressed={i === index} onClick={() => onChange(i)}>{label(t)}</button>)}
    </div>
  );
}

export default function MediaPanel({ revision }: { revision: number }) {
  const [media, setMedia] = useState<{ data: Media | null; error: boolean } | null>(null);
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

  return (
    <section className="media-panel" aria-label="ภาพเรดาร์และแผนที่คาดการณ์">
      <div className="legend-heading"><h3><Icon name="radar" />ภาพเรดาร์และแผนที่คาดการณ์</h3></div>
      <div className="dam-sort media-tabs" role="tablist">
        {TABS.map((t) => <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} className={tab === t.id ? "is-active" : ""} onClick={() => setTab(t.id)}>{t.th}</button>)}
      </div>
      {!media && <p className="search-feedback" role="status">กำลังโหลดภาพ…</p>}
      {media?.error && !data && <p className="search-feedback data-notice" role="alert">โหลดภาพไม่สำเร็จ</p>}
      {data && tab === "radar" && (data.radar.length && radar ? <>
        <label className="sr-only" htmlFor="radar-select">เลือกเรดาร์</label>
        <select id="radar-select" className="media-select" value={radar.id} onChange={(e) => setRadarId(e.target.value)}>
          {data.radar.map((r) => <option key={r.id} value={r.id}>{r.name_th}</option>)}
        </select>
        <Picture item={radar} alt={`ภาพเรดาร์ฝน${radar.name_th}`} caption={`${radar.agency_th} · ${when(radar.time)} น.`} />
        <p className="source-note">สีเขียว-เหลือง = ฝนเล็กน้อยถึงปานกลาง · สีแดง-ม่วง = ฝนหนักมาก · แสดงเฉพาะเรดาร์ที่อัปเดตภายใน 3 ชั่วโมง ({data.radar.length} สถานี)</p>
      </> : <p className="search-feedback">ตอนนี้ไม่มีภาพเรดาร์ที่อัปเดตภายใน 3 ชั่วโมง</p>)}
      {data && tab === "forecast" && <>
        <div className="media-steps" role="group" aria-label="เลือกพื้นที่">
          {Object.entries(data.rain_forecast).filter(([, v]) => v.days.length).map(([k, v]) => (
            <button key={k} type="button" className={region === k ? "is-active" : ""} aria-pressed={region === k} onClick={() => { setRegion(k); setDay(0); }}>{v.label_th}</button>
          ))}
        </div>
        {forecast && forecastDay ? <>
          <Steps items={forecast.days} index={Math.min(day, forecast.days.length - 1)} onChange={setDay} label={(d) => when(d.time, false)} />
          <Picture item={forecastDay} alt={`แผนที่คาดการณ์ฝน${forecast.label_th} ${when(forecastDay.time, false)}`} caption={`คาดการณ์ฝนสะสมรายวัน ${when(forecastDay.time, false)}`} />
          <p className="source-note">แบบจำลอง WRF-ROMS ของ สสน. · เป็นการคาดการณ์ ไม่ใช่ประกาศเตือนภัย</p>
        </> : <p className="search-feedback">ยังไม่มีแผนที่คาดการณ์ฝนรอบล่าสุด</p>}
      </>}
      {data && tab === "storm" && (data.storm.length ? <div className="media-grid">
        {data.storm.map((s) => <Picture key={s.image} item={s} alt={`${s.region_th} ${s.source_th}`} caption={`${s.region_th} · ${s.source_th} · ${when(s.time)} น.`} />)}
      </div> : <p className="search-feedback">ยังไม่มีแผนที่ติดตามพายุที่อัปเดตใน 2 วัน</p>)}
      {data && tab === "wave" && (data.wave.length ? <>
        <Steps items={data.wave} index={Math.min(wave, data.wave.length - 1)} onChange={setWave} label={(w) => when(w.time, false)} />
        <Picture item={data.wave[Math.min(wave, data.wave.length - 1)]} alt="แผนที่คาดการณ์ความสูงคลื่น" caption={`คาดการณ์ความสูงคลื่น ${when(data.wave[Math.min(wave, data.wave.length - 1)].time)} น.`} />
        <p className="source-note">แบบจำลอง SWAN ของ สสน. · ใช้ประกอบการตัดสินใจออกเรือ ร่วมกับประกาศกรมอุตุนิยมวิทยา</p>
      </> : <p className="search-feedback">ยังไม่มีแผนที่คาดการณ์คลื่นรอบล่าสุด</p>)}
      <p className="source-note">ภาพจาก ThaiWater (สสน.) และหน่วยงานเจ้าของข้อมูล · แตะภาพเพื่อดูขนาดเต็ม</p>
    </section>
  );
}
