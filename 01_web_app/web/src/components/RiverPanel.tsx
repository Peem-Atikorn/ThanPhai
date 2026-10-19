"use client";

import { useEffect, useState } from "react";
import { AGENCY_TH, api, River, riverColor, StationGraph } from "@/lib/api";
import Icon from "@/components/Icon";
import LineChart from "@/components/LineChart";

const number = new Intl.NumberFormat("th-TH");
export type RiverSort = "chain" | "overflow" | "flow";

export function riverTime(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("th-TH", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "Asia/Bangkok" }) + " น.";
}

function bankText(r: River) {
  if (r.above_bank_m == null) return "ไม่ทราบระดับตลิ่ง";
  return r.above_bank_m > 0 ? `ล้นตลิ่ง ${r.above_bank_m.toFixed(2)} ม.` : `ต่ำกว่าตลิ่ง ${(-r.above_bank_m).toFixed(2)} ม.`;
}

function trendText(change: number | null) {
  if (change == null) return null;
  if (Math.abs(change) < 0.01) return "ทรงตัว";
  return `${change > 0 ? "เพิ่มขึ้น" : "ลดลง"} ${Math.round(Math.abs(change) * 100)} ซม./ชม.`;
}

export function RiverRow({ river, active, onSelect }: { river: River; active: boolean; onSelect: (r: River) => void }) {
  return (
    <li>
      <button type="button" className={"dam-row " + (active ? "is-active" : "")} aria-pressed={active} onClick={() => onSelect(river)}>
        <span className="dam-name">
          <strong>{river.code} {river.name_th}</strong>
          <small>{river.river_th ? river.river_th + " · " : ""}จ.{river.province_th}</small>
        </span>
        <span className="dam-value">
          {river.discharge_m3s != null ? <><strong>{number.format(Math.round(river.discharge_m3s))}</strong><small>ลบ.ม./วิ</small></> : <strong>—</strong>}
        </span>
        <span className="river-status" style={{ color: riverColor(river) }}>{bankText(river)}{river.capacity_percent != null && <> · ใช้ความจุลำน้ำ {river.capacity_percent}%</>}</span>
      </button>
    </li>
  );
}

function shortTime(t: string) {
  // "2026-10-19 17:00" (Thai local time from ThaiWater)
  const [d, hm] = t.split(" ");
  const [, m, day] = d.split("-");
  return { x: new Date(t.replace(" ", "T") + ":00+07:00").getTime(), label: `${+day}/${+m} ${hm?.slice(0, 2)}น.` };
}

function StationCharts({ graph }: { graph: StationGraph }) {
  const pts = graph.points.map((p) => ({ ...p, x: shortTime(p.time).x }));
  const level = pts.filter((p) => p.level_msl != null).map((p) => [p.x, p.level_msl!] as [number, number]);
  const flow = pts.filter((p) => p.discharge_m3s != null).map((p) => [p.x, p.discharge_m3s!] as [number, number]);
  // One tick per day, at midnight.
  const days = Array.from(new Set(graph.points.map((p) => p.time.slice(0, 10))));
  const ticks = days.map((d) => shortTime(d + " 00:00")).map((t) => ({ x: t.x, label: t.label.replace(" 00น.", "") }));
  return (
    <div className="station-charts">
      {level.length > 1 && <LineChart ariaLabel="กราฟระดับน้ำย้อนหลัง" unit="ม.รทก." xTicks={ticks}
        series={[{ label: "ระดับน้ำ", color: "#2f7fb5", points: level }]}
        refs={graph.bank_msl != null ? [{ y: graph.bank_msl, label: "ระดับตลิ่ง", color: "#d7263d" }] : []} />}
      {flow.length > 1 && <LineChart ariaLabel="กราฟปริมาณน้ำไหลผ่านย้อนหลัง" unit="ลบ.ม./วิ" xTicks={ticks}
        series={[{ label: "ปริมาณน้ำไหลผ่าน", color: "#0f766e", points: flow }]}
        refs={graph.qmax_m3s != null ? [{ y: graph.qmax_m3s, label: "ความจุลำน้ำ", color: "#d7263d" }] : []} />}
    </div>
  );
}

export function RiverDetail({ river, onClose }: { river: River; onClose: () => void }) {
  const [graph, setGraph] = useState<{ id: string; data: StationGraph | null; error: boolean } | null>(null);
  const id = river.id;
  useEffect(() => {
    if (!id) return;
    let cancelled = false;
    api.riverGraph(id, 3)
      .then((data) => !cancelled && setGraph({ id, data, error: false }))
      .catch(() => !cancelled && setGraph({ id, data: null, error: true }));
    return () => { cancelled = true; };
  }, [id]);
  const current = graph?.id === id ? graph : null;
  const trend = trendText(river.level_change_m);
  return (
    <div className="dam-detail">
      <div className="area-title">
        <span className="area-icon" style={{ color: riverColor(river) }}><Icon name="river" /></span>
        <div><h2>{river.code} {river.name_th}</h2><p>{river.river_th ? river.river_th + " · " : ""}{river.amphoe_th ? "อ." + river.amphoe_th + " " : ""}จ.{river.province_th}</p></div>
        <button className="icon-button" aria-label="ปิดรายละเอียดสถานี" onClick={onClose}><Icon name="close" /></button>
      </div>
      <div className="dam-storage">
        <div>
          <strong>{river.discharge_m3s != null ? number.format(Math.round(river.discharge_m3s)) : "—"}</strong>
          <span className="flow-unit">ลบ.ม./วินาที</span>
        </div>
        {river.capacity_percent != null && <>
          <span className="dam-bar" aria-hidden><span style={{ width: Math.min(river.capacity_percent, 150) / 1.5 + "%", background: riverColor(river) }} /></span>
          <p>ใช้ความจุลำน้ำ {river.capacity_percent}% (ลำน้ำรับได้ราว {number.format(Math.round(river.qmax_m3s!))} ลบ.ม./วินาที ก่อนล้นตลิ่ง)</p>
        </>}
      </div>
      <ul className="dam-flows">
        <li><span>สถานะ</span><strong style={{ color: riverColor(river) }}>{river.status_th}</strong></li>
        <li><span>เทียบกับตลิ่ง</span><strong>{bankText(river)}</strong></li>
        {river.level_msl != null && <li><span>ระดับน้ำ</span><strong>{river.level_msl.toFixed(2)}<small> ม.รทก.</small></strong>{trend && <small>{trend}</small>}</li>}
      </ul>
      <div className="dam-totals">
        <h3>ย้อนหลัง 3 วัน (รายชั่วโมง)</h3>
        {!current && <p role="status">กำลังโหลดกราฟ…</p>}
        {current?.error && <p className="data-notice">โหลดกราฟไม่สำเร็จ</p>}
        {current?.data && <StationCharts graph={current.data} />}
      </div>
      <p className="source-note">ข้อมูล ณ {riverTime(river.time)} · ThaiWater (สสน.){river.agency && <>, สถานีของ{AGENCY_TH[river.agency] ?? river.agency}</>}</p>
    </div>
  );
}

export function sortRivers(rivers: River[], sort: RiverSort): River[] {
  if (sort === "chain") return rivers.filter((r) => r.chain_order != null).sort((a, b) => a.chain_order! - b.chain_order!);
  if (sort === "overflow") return rivers.filter((r) => r.status === "OVERFLOW" || r.status === "NEAR_BANK").sort((a, b) => (b.above_bank_m ?? -99) - (a.above_bank_m ?? -99));
  return rivers.filter((r) => r.discharge_m3s != null).sort((a, b) => b.discharge_m3s! - a.discharge_m3s!).slice(0, 30);
}
