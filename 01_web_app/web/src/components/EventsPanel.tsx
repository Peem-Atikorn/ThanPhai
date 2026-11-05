"use client";

import { t as tr, getLocale } from "@/lib/i18n";
import { useEffect, useState } from "react";
import { api, Earthquake, RegionalEvent } from "@/lib/api";
import Icon from "@/components/Icon";
function when(iso: string) {
    return new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z").toLocaleString(getLocale(), { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Bangkok" });
}
const ALERT_CLASS: Record<string, string> = { Red: "level-CRITICAL", Orange: "level-WARNING", Green: "level-NORMAL" };
/** Recent earthquakes (TMD + USGS, unless `quakes` is false) and GDACS events around South-East Asia. */
export default function EventsPanel({ revision, quakes: showQuakes = true }: {
    revision: number;
    quakes?: boolean;
}) {
    const [quakes, setQuakes] = useState<Earthquake[] | null>(null);
    const [events, setEvents] = useState<RegionalEvent[] | null>(null);
    const [error, setError] = useState(false);
    useEffect(() => {
        let cancelled = false;
        Promise.all([api.earthquakes(), api.regionalEvents()])
            .then(([q, e]) => { if (!cancelled) {
            setQuakes(q.earthquakes);
            setEvents(e.events);
            setError(false);
        } })
            .catch(() => !cancelled && setError(true));
        return () => { cancelled = true; };
    }, [revision]);
    // Quakes in Thailand are listed at any size (an M2.3 under Phayao is felt there and makes the news);
    // abroad, only M2.5 and up. TMD names a country for quakes abroad; USGS writes "…, Thailand".
    const inThailand = (q: Earthquake) => q.place_th ? !/ประเทศ|มณฑล/.test(q.place_th) : /thailand/i.test(q.place ?? "");
    const felt = quakes?.filter((q) => q.magnitude >= 2.5 || inThailand(q)).slice(0, 12) ?? [];
    return (<section className="media-panel events-panel" aria-label={tr("แผ่นดินไหวและเหตุการณ์ในภูมิภาค")}>
      {tr(error && <p className="data-notice">{tr("โหลดรายการไม่สำเร็จ ลองใหม่ภายหลัง")}</p>)}
      {tr(showQuakes && <>
      <h3><Icon name="earthquake"/>{tr("แผ่นดินไหว 7 วันล่าสุด")}</h3>
      {tr(quakes && felt.length === 0 && <p className="source-note">{tr("ไม่มีแผ่นดินไหวในประเทศไทย หรือขนาด 2.5 ขึ้นไปในภูมิภาค")}</p>)}
      <ul className="event-list">
        {tr(felt.map((q) => (<li key={q.id ?? q.time}>
            <span className={"event-mag " + (q.magnitude >= 6 ? "level-CRITICAL" : q.magnitude >= 5 ? "level-WARNING" : q.magnitude >= 4 ? "level-WATCH" : "level-NORMAL")}>{tr(q.magnitude.toFixed(1))}</span>
            <div>
              <strong>{tr(q.place_th || q.place)}</strong>
              {tr(q.where_th && <small>{tr(q.where_th)}</small>)}
              <small>{tr(when(q.time))}{tr(" น. \u00B7 ลึก ")}{tr(q.depth_km ?? "—")}{tr(" กม. \u00B7 ")}{tr(q.source)}{tr(q.tsunami ? " · มีการประเมินสึนามิ" : "")}{tr(q.url && <> · <a href={q.url} target="_blank" rel="noreferrer noopener">{tr("รายละเอียด")}</a></>)}</small>
            </div>
          </li>)))}
      </ul>
      </>)}
      <h3><Icon name="map"/>{tr("เหตุการณ์ในภูมิภาค (GDACS)")}</h3>
      {tr(events && events.length === 0 && <p className="source-note">{tr("ไม่มีเหตุการณ์ในเอเชียตะวันออกเฉียงใต้ช่วง 7 วันที่ผ่านมา")}</p>)}
      <ul className="event-list">
        {tr(events?.slice(0, 8).map((e) => (<li key={e.id}>
            <span className={"risk-badge " + (ALERT_CLASS[e.alert ?? ""] ?? "level-NONE")}>{tr(e.alert_th ?? "—")}</span>
            <div>
              <strong>{tr(e.type_th)}: {tr(e.name)}</strong>
              <small>{tr(e.countries)}{tr(e.severity_text ? " · " + e.severity_text : "")}</small>
              <small>{tr(e.current ? "ยังเกิดอยู่" : "สิ้นสุดแล้ว")}{tr(" \u00B7 ล่าสุด ")}{tr(when(e.to))}{tr(" น.")}{tr(e.url && <> · <a href={e.url} target="_blank" rel="noreferrer noopener">{tr("รายงาน")}</a></>)}</small>
            </div>
          </li>)))}
      </ul>
      <p className="source-note">{tr("ที่มา: ")}{tr(showQuakes ? "กรมอุตุนิยมวิทยา, USGS, " : "")}{tr("GDACS \u00B7 ระดับสีของ GDACS คือระดับผลกระทบที่ GDACS ประเมิน ไม่ใช่ระดับเตือนภัยของทันภัย")}</p>
    </section>);
}
