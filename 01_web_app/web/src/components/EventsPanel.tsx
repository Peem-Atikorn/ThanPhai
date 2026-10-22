"use client";

import { useEffect, useState } from "react";
import { api, Earthquake, RegionalEvent } from "@/lib/api";
import Icon from "@/components/Icon";

function when(iso: string) {
  return new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z").toLocaleString("th-TH", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Bangkok" });
}

const ALERT_CLASS: Record<string, string> = { Red: "level-CRITICAL", Orange: "level-WARNING", Green: "level-NORMAL" };

/** Recent earthquakes (TMD + USGS, unless `quakes` is false) and GDACS events around South-East Asia. */
export default function EventsPanel({ revision, quakes: showQuakes = true }: { revision: number; quakes?: boolean }) {
  const [quakes, setQuakes] = useState<Earthquake[] | null>(null);
  const [events, setEvents] = useState<RegionalEvent[] | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.earthquakes(), api.regionalEvents()])
      .then(([q, e]) => { if (!cancelled) { setQuakes(q.earthquakes); setEvents(e.events); setError(false); } })
      .catch(() => !cancelled && setError(true));
    return () => { cancelled = true; };
  }, [revision]);

  const felt = quakes?.filter((q) => q.magnitude >= 2.5).slice(0, 12) ?? [];
  return (
    <section className="media-panel events-panel" aria-label="แผ่นดินไหวและเหตุการณ์ในภูมิภาค">
      {error && <p className="data-notice">โหลดรายการไม่สำเร็จ ลองใหม่ภายหลัง</p>}
      {showQuakes && <>
      <h3><Icon name="earthquake" />แผ่นดินไหว 7 วันล่าสุด</h3>
      {quakes && felt.length === 0 && <p className="source-note">ไม่มีแผ่นดินไหวขนาด 2.5 ขึ้นไปในภูมิภาค</p>}
      <ul className="event-list">
        {felt.map((q) => (
          <li key={q.id ?? q.time}>
            <span className={"event-mag " + (q.magnitude >= 6 ? "level-CRITICAL" : q.magnitude >= 5 ? "level-WARNING" : q.magnitude >= 4 ? "level-WATCH" : "level-NORMAL")}>{q.magnitude.toFixed(1)}</span>
            <div>
              <strong>{q.place_th || q.place}</strong>
              {q.where_th && <small>{q.where_th}</small>}
              <small>{when(q.time)} น. · ลึก {q.depth_km ?? "—"} กม. · {q.source}{q.tsunami ? " · มีการประเมินสึนามิ" : ""}{q.url && <> · <a href={q.url} target="_blank" rel="noreferrer noopener">รายละเอียด</a></>}</small>
            </div>
          </li>
        ))}
      </ul>
      </>}
      <h3><Icon name="map" />เหตุการณ์ในภูมิภาค (GDACS)</h3>
      {events && events.length === 0 && <p className="source-note">ไม่มีเหตุการณ์ในเอเชียตะวันออกเฉียงใต้ช่วง 7 วันที่ผ่านมา</p>}
      <ul className="event-list">
        {events?.slice(0, 8).map((e) => (
          <li key={e.id}>
            <span className={"risk-badge " + (ALERT_CLASS[e.alert ?? ""] ?? "level-NONE")}>{e.alert_th ?? "—"}</span>
            <div>
              <strong>{e.type_th}: {e.name}</strong>
              <small>{e.countries}{e.severity_text ? " · " + e.severity_text : ""}</small>
              <small>{e.current ? "ยังเกิดอยู่" : "สิ้นสุดแล้ว"} · ล่าสุด {when(e.to)} น.{e.url && <> · <a href={e.url} target="_blank" rel="noreferrer noopener">รายงาน</a></>}</small>
            </div>
          </li>
        ))}
      </ul>
      <p className="source-note">ที่มา: {showQuakes ? "กรมอุตุนิยมวิทยา, USGS, " : ""}GDACS · ระดับสีของ GDACS คือระดับผลกระทบที่ GDACS ประเมิน ไม่ใช่ระดับเตือนภัยของทันภัย</p>
    </section>
  );
}
