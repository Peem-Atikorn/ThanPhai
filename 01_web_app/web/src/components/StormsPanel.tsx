"use client";

import { useEffect, useState } from "react";
import { api, StormsResponse } from "@/lib/api";
import { getLocale, t as tr } from "@/lib/i18n";
import Icon from "@/components/Icon";

export default function StormsPanel({ revision }: { revision: number }) {
  const [result, setResult] = useState<{ data?: StormsResponse; error?: boolean; revision: number } | null>(null);
  useEffect(() => {
    let cancelled = false;
    api.storms().then(data => { if (!cancelled) setResult({ data, revision }); })
      .catch(() => { if (!cancelled) setResult({ error: true, revision }); });
    return () => { cancelled = true; };
  }, [revision]);
  const current = result?.revision === revision ? result : null;
  const data = current?.data;
  const pending = !!data?.inputs_pending?.includes("thaiwater_storms");
  const degraded = !!data?.degraded_services?.includes("thaiwater_storms");
  return <section className="media-panel events-panel storms-panel" aria-label={tr("พายุหมุนที่ สสน. ติดตาม")} aria-busy={!current || pending}>
    <h3><Icon name="tropical_cyclone"/>{tr("พายุหมุนที่ สสน. ติดตาม")}</h3>
    {!current && <p className="search-feedback" role="status">{tr("กำลังโหลดรายชื่อพายุ…")}</p>}
    {current?.error && <p className="data-notice" role="alert">{tr("โหลดรายชื่อพายุไม่สำเร็จ กรุณากดโหลดข้อมูลแผนที่ใหม่")}</p>}
    {pending && <p className="source-note" role="status">{tr("กำลังรอข้อมูลพายุจากแหล่งข้อมูล")}</p>}
    {degraded && <p className="data-notice">{tr("ข้อมูลพายุบางแหล่งไม่พร้อมใช้งาน")}</p>}
    {data && !data.storms.length && !pending && !degraded && <p className="source-note">{tr("ไม่มีพายุที่ สสน. ติดตามในข้อมูลชุดนี้")}</p>}
    <ul className="event-list">{data?.storms.map((storm, index) => <li key={`${storm.name}-${storm.time}-${index}`}>
      <div>
        <strong>{storm.name}</strong>
        <span>{tr("ระดับพายุ: ")}{storm.category?.category ?? "—"}</span>
        <span>{tr("ความเร็วลม: ")}{storm.wind_kmh ?? "—"}{tr(" กม./ชม.")}</span>
        <small>{tr("ละติจูด: ")}{storm.lat ?? "—"}{tr(" · ลองจิจูด: ")}{storm.lon ?? "—"}</small>
        <small>{tr("เวลาข้อมูล: ")}{new Date(storm.time).toLocaleString(getLocale(), {
          day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Bangkok",
        })}{tr(" น. (เวลาไทย)")}</small>
      </div>
    </li>)}</ul>
    {data && <p className="source-note">{tr("ที่มา: ")}{data.source}</p>}
    <p className="source-note">{tr("ระดับพายุจาก สสน. เป็นข้อมูลประกอบ ไม่ใช่ระดับเตือนภัยของทันภัย")}</p>
  </section>;
}
