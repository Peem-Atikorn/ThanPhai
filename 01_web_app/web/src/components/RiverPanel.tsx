"use client";

import { t as tr, getLocale } from "@/lib/i18n";
import { useEffect, useState } from "react";
import { AGENCY_TH, api, River, riverColor, RiverForecast, StationGraph } from "@/lib/api";
import Icon from "@/components/Icon";
import LineChart from "@/components/LineChart";
const number = new Intl.NumberFormat(getLocale());
export type RiverSort = "chain" | "overflow" | "flow";
export function riverTime(iso: string | null) {
    if (!iso)
        return "—";
    return new Date(iso).toLocaleString(getLocale(), { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Bangkok" }) + tr(" น.");
}
function bankText(r: River) {
    if (r.above_bank_m == null)
        return "ไม่ทราบระดับตลิ่ง";
    return r.above_bank_m > 0 ? tr("ล้นตลิ่ง ") + tr(r.above_bank_m.toFixed(2)) + tr(" ม.") : tr("ต่ำกว่าตลิ่ง ") + tr((-r.above_bank_m).toFixed(2)) + tr(" ม.");
}
function trendText(change: number | null) {
    if (change == null)
        return null;
    if (Math.abs(change) < 0.01)
        return "ทรงตัว";
    return tr("") + tr(change > 0 ? "เพิ่มขึ้น" : "ลดลง") + tr(" ") + tr(Math.round(Math.abs(change) * 100)) + tr(" ซม./ชม.");
}
export function RiverRow({ river, active, onSelect }: {
    river: River;
    active: boolean;
    onSelect: (r: River) => void;
}) {
    return (<li>
      <button type="button" className={"dam-row " + (active ? "is-active" : "")} aria-pressed={active} onClick={() => onSelect(river)}>
        <span className="dam-name">
          <strong>{tr(river.code)} {tr(river.name_th)}</strong>
          <small>{tr(river.river_th ? river.river_th + " · " : "")}{tr("จ.")}{tr(river.province_th)}</small>
        </span>
        <span className="dam-value">
          {tr(river.discharge_m3s != null ? <><strong>{tr(number.format(Math.round(river.discharge_m3s)))}</strong><small>{tr("ลบ.ม./วิ")}</small></> : <strong>—</strong>)}
        </span>
        <span className="river-status" style={{ color: riverColor(river) }}>{tr(bankText(river))}{tr(river.capacity_percent != null && <>{tr(" \u00B7 ใช้ความจุลำน้ำ ")}{tr(river.capacity_percent)}%</>)}</span>
      </button>
    </li>);
}
function shortTime(t: string) {
    // "2026-10-19 17:00" (Thai local time from ThaiWater)
    const [d, hm] = t.split(" ");
    const [, m, day] = d.split("-");
    return { x: new Date(t.replace(" ", "T") + ":00+07:00").getTime(), label: tr("") + tr(+day) + tr("/") + tr(+m) + tr(" ") + tr(hm?.slice(0, 2)) + tr("น.") };
}
// The forecast line continues from the last point of the level line (or, without one, from the hour the model started
// at); dashed and amber so it never reads as a reading.
export function forecastSeries(forecast: RiverForecast | null, last?: [number, number]): [number, number][] {
    if (!forecast?.available || !forecast.from_time || forecast.level_msl == null || !forecast.points?.length)
        return [];
    const start: [number, number] = last ?? [shortTime(forecast.from_time).x, forecast.level_msl];
    return [start, ...forecast.points.map((p) => [shortTime(p.time).x, p.level_msl] as [number, number])];
}
function StationCharts({ graph, forecast }: {
    graph: StationGraph;
    forecast: RiverForecast | null;
}) {
    const pts = graph.points.map((p) => ({ ...p, x: shortTime(p.time).x }));
    const level = pts.filter((p) => p.level_msl != null).map((p) => [p.x, p.level_msl!] as [
        number,
        number
    ]);
    const flow = pts.filter((p) => p.discharge_m3s != null).map((p) => [p.x, p.discharge_m3s!] as [
        number,
        number
    ]);
    const ahead = forecastSeries(forecast, level.at(-1));
    // One tick per day, at midnight (the forecast can reach into the next day).
    const days = Array.from(new Set([...graph.points.map((p) => p.time), ...(forecast?.points ?? []).map((p) => p.time)].map((t) => t.slice(0, 10))));
    const ticks = days.map((d) => shortTime(d + " 00:00")).map((t) => ({ x: t.x, label: t.label.replace(" 00น.", "") }));
    const levelSeries = [{ label: "ระดับน้ำ", color: "#2f7fb5", points: level }, ...(ahead.length > 1 ? [{ label: "ค่าคาดการณ์จากแบบจำลอง", color: "#d97706", points: ahead, dashed: true }] : [])];
    return (<div className="station-charts">
      {tr(level.length > 1 && <LineChart ariaLabel={ahead.length > 1 ? "กราฟระดับน้ำย้อนหลังและค่าคาดการณ์" : "กราฟระดับน้ำย้อนหลัง"} unit="ม.รทก." xTicks={ticks} series={levelSeries} refs={graph.bank_msl != null ? [{ y: graph.bank_msl, label: "ระดับตลิ่ง", color: "#d7263d" }] : []}/>)}
      {tr(flow.length > 1 && <LineChart ariaLabel="กราฟปริมาณน้ำไหลผ่านย้อนหลัง" unit="ลบ.ม./วิ" xTicks={ticks} series={[{ label: "ปริมาณน้ำไหลผ่าน", color: "#0f766e", points: flow }]} refs={graph.qmax_m3s != null ? [{ y: graph.qmax_m3s, label: "ความจุลำน้ำ", color: "#d7263d" }] : []}/>)}
    </div>);
}
const cm = (m: number) => Math.round(Math.abs(m) * 100);
/** The forecast in words, labelled as a model and never as a warning level. */
export function ForecastNote({ forecast }: {
    forecast: RiverForecast | null;
}) {
    if (forecast && !forecast.available && forecast.reason === "not_chosen")
        return <p className="source-note">{tr("สถานีนี้ไม่มีค่าคาดการณ์: ในปีที่ใช้ตรวจ แบบจำลองไม่แม่นกว่าการถือว่าระดับน้ำคงเดิม")}</p>;
    // The model only runs on a reading at most 3 h old; RID stations upload in batches, so overnight the latest
    // reading can be older and the forecast disappears until the next batch.
    if (forecast && !forecast.available && forecast.reason === "no_recent_readings")
        return <p className="source-note">{tr("ยังไม่มีค่าคาดการณ์ตอนนี้: ข้อมูลล่าสุดของสถานีนี้เก่ากว่า 3 ชั่วโมง (หน่วยงานเจ้าของสถานีส่งข้อมูลเป็นรอบ) ค่าคาดการณ์จะกลับมาเมื่อมีข้อมูลรอบใหม่")}</p>;
    if (!forecast?.available || !forecast.points?.length)
        return null;
    const errors = forecast.points.map((p) => p.test_mae_m).filter((e): e is number => e != null);
    return (<div className="river-forecast">
      <h4>{tr("ค่าคาดการณ์จากแบบจำลอง (ไม่ใช่ระดับเตือนภัย)")}</h4>
      <ul>
        {forecast.points.map((p) => (<li key={p.hours}>
          {/* A clock time, not "in N hours": the model counts from the start of the latest hour, not from now. */}
          <span>{tr("ราว ")}{tr(shortTime(p.time).label)}</span>
          <strong>{p.level_msl.toFixed(2)}<small>{tr(" ม.รทก.")}</small></strong>
          <small>{tr(Math.abs(p.change_m) < 0.01 ? "ทรงตัว" : p.change_m > 0 ? "ขึ้น " : "ลด ")}{Math.abs(p.change_m) < 0.01 ? "" : <>{cm(p.change_m)}{tr(" ซม.")}</>}{tr(" · ")}{tr(p.above_bank_m > 0 ? "สูงกว่าตลิ่ง " : "ต่ำกว่าตลิ่ง ")}{Math.abs(p.above_bank_m).toFixed(2)}{tr(" ม.")}</small>
        </li>))}
      </ul>
      <small>{tr("คำนวณจากระดับน้ำย้อนหลัง 3 วันของสถานีนี้เท่านั้น ไม่รู้ฝนที่จะตกหรือน้ำจากต้นน้ำ")}{errors.length > 0 && <>{tr(" · ในปีทดสอบคลาดเคลื่อนเฉลี่ย ")}{Math.min(...errors.map(cm))}{tr("–")}{Math.max(...errors.map(cm))}{tr(" ซม.")}</>}{tr(" · ระดับเตือนภัยยังมาจากเกณฑ์ของทันภัย")}</small>
    </div>);
}
export function RiverDetail({ river, onClose }: {
    river: River;
    onClose: () => void;
}) {
    const [graph, setGraph] = useState<{
        id: string;
        data: StationGraph | null;
        error: boolean;
    } | null>(null);
    const [forecast, setForecast] = useState<{
        id: string;
        data: RiverForecast | null;
    } | null>(null);
    const id = river.id;
    useEffect(() => {
        if (!id)
            return;
        let cancelled = false;
        api.riverGraph(id, 3)
            .then((data) => !cancelled && setGraph({ id, data, error: false }))
            .catch(() => !cancelled && setGraph({ id, data: null, error: true }));
        // The forecast reuses the graph's cached request on the server; a failure just leaves it out.
        api.riverForecast(id)
            .then((data) => !cancelled && setForecast({ id, data }))
            .catch(() => !cancelled && setForecast({ id, data: null }));
        return () => { cancelled = true; };
    }, [id]);
    const current = graph?.id === id ? graph : null;
    const currentForecast = forecast?.id === id ? forecast.data : null;
    const trend = trendText(river.level_change_m);
    return (<div className="dam-detail">
      <div className="area-title">
        <span className="area-icon" style={{ color: riverColor(river) }}><Icon name="river"/></span>
        <div><h2>{tr(river.code)} {tr(river.name_th)}</h2><p>{tr(river.river_th ? river.river_th + " · " : "")}{tr(river.amphoe_th ? "อ." + river.amphoe_th + " " : "")}{tr("จ.")}{tr(river.province_th)}</p></div>
        <button className="icon-button" aria-label={tr("ปิดรายละเอียดสถานี")} onClick={onClose}><Icon name="close"/></button>
      </div>
      <div className="dam-storage">
        {/* Some stations (e.g. T.13) have no flow gauge in ThaiWater; a bare "—" beside the unit read as a missing value. */}
        {tr(river.discharge_m3s != null
            ? <div>
                <strong>{tr(number.format(Math.round(river.discharge_m3s)))}</strong>
                <span className="flow-unit">{tr("ลบ.ม./วินาที")}</span>
              </div>
            : <p className="data-notice">{tr("สถานีนี้ไม่รายงานปริมาณน้ำไหลผ่าน (ลบ.ม./วินาที) ใน ThaiWater จึงแสดงเฉพาะระดับน้ำ")}</p>)}
        {tr(river.capacity_percent != null && <>
          <span className="dam-bar" aria-hidden><span style={{ width: Math.min(river.capacity_percent, 150) / 1.5 + "%", background: riverColor(river) }}/></span>
          <p>{tr("ใช้ความจุลำน้ำ ")}{tr(river.capacity_percent)}{tr("% (ลำน้ำรับได้ราว ")}{tr(number.format(Math.round(river.qmax_m3s!)))}{tr(" ลบ.ม./วินาที ก่อนล้นตลิ่ง)")}</p>
        </>)}
      </div>
      <ul className="dam-flows">
        <li><span>{tr("สถานะ")}</span><strong style={{ color: riverColor(river) }}>{tr(river.status_th)}</strong></li>
        <li><span>{tr("เทียบกับตลิ่ง")}</span><strong>{tr(bankText(river))}</strong></li>
        {tr(river.level_msl != null && <li><span>{tr("ระดับน้ำ")}</span><strong>{tr(river.level_msl.toFixed(2))}<small>{tr(" ม.รทก.")}</small></strong>{tr(trend && <small>{tr(trend)}</small>)}</li>)}
      </ul>
      <div className="dam-totals">
        <h3>{tr("ย้อนหลัง 3 วัน (รายชั่วโมง)")}</h3>
        {tr(!current && <p role="status">{tr("กำลังโหลดกราฟ\u2026")}</p>)}
        {tr(current?.error && <p className="data-notice">{tr("โหลดกราฟไม่สำเร็จ")}</p>)}
        {tr(current?.data && <StationCharts graph={current.data} forecast={currentForecast}/>)}
        {tr(current?.data && <ForecastNote forecast={currentForecast}/>)}
      </div>
      <p className="source-note">{tr("ข้อมูล ณ ")}{tr(riverTime(river.time))}{tr(" \u00B7 ThaiWater (สสน.)")}{tr(river.agency && <>{tr(", สถานีของ")}{tr(AGENCY_TH[river.agency] ?? river.agency)}</>)}</p>
    </div>);
}
export function sortRivers(rivers: River[], sort: RiverSort): River[] {
    if (sort === "chain")
        return rivers.filter((r) => r.chain_order != null).sort((a, b) => a.chain_order! - b.chain_order!);
    if (sort === "overflow")
        return rivers.filter((r) => r.status === "OVERFLOW" || r.status === "NEAR_BANK").sort((a, b) => (b.above_bank_m ?? -99) - (a.above_bank_m ?? -99));
    return rivers.filter((r) => r.discharge_m3s != null).sort((a, b) => b.discharge_m3s! - a.discharge_m3s!).slice(0, 30);
}
