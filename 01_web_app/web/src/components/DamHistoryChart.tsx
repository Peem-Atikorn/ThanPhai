"use client";

import { useEffect, useState } from "react";
import { api, DamHistory } from "@/lib/api";
import LineChart from "@/components/LineChart";

type Series = "storage" | "released" | "inflow";
const LABELS: Record<Series, string> = { storage: "น้ำในอ่าง", released: "น้ำระบายรายวัน", inflow: "น้ำไหลเข้ารายวัน" };
const MONTHS = ["ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค."];

/** Day of year (0-365) from "YYYY-MM-DD" or "MM-DD", so different years share one x axis. */
function dayOfYear(date: string) {
  const md = date.length > 5 ? date.slice(5, 10) : date;
  const [m, d] = md.split("-").map(Number);
  return Math.round((Date.UTC(2024, m - 1, d) - Date.UTC(2024, 0, 1)) / 86_400_000);
}

const ticks = [0, 2, 4, 6, 8, 10].map((m) => ({ x: dayOfYear(`${String(m + 1).padStart(2, "0")}-01`), label: MONTHS[m] }));

export default function DamHistoryChart({ damId }: { damId: string }) {
  const [series, setSeries] = useState<Series>("storage");
  const [state, setState] = useState<{ key: string; data: DamHistory | null; error: boolean } | null>(null);
  const key = damId + ":" + series;
  useEffect(() => {
    let cancelled = false;
    api.damHistory(damId, series)
      .then((data) => !cancelled && setState({ key, data, error: false }))
      .catch(() => !cancelled && setState({ key, data: null, error: true }));
    return () => { cancelled = true; };
  }, [damId, series, key]);
  const current = state?.key === key ? state : null;
  const data = current?.data;
  const years = data?.series.filter((s) => s.values.length) ?? [];
  const colors = ["#9aa5b1", "#2f7fb5"]; // last year grey, this year blue
  const toPoints = (values: [string, number][]) => values.map(([d, v]) => [dayOfYear(d), v] as [number, number]);
  const hasBand = series === "storage" && !!data?.upper_rule_curve.length && !!data?.lower_rule_curve.length;

  return (
    <div className="dam-totals">
      <h3>เทียบกับปีที่แล้ว</h3>
      <div className="dam-sort chart-switch" role="group" aria-label="เลือกข้อมูลกราฟ">
        {(Object.keys(LABELS) as Series[]).map((s) => (
          <button key={s} type="button" className={series === s ? "is-active" : ""} aria-pressed={series === s} onClick={() => setSeries(s)}>{LABELS[s]}</button>
        ))}
      </div>
      {!current && <p role="status">กำลังโหลดกราฟ…</p>}
      {current?.error && <p className="data-notice">โหลดกราฟไม่สำเร็จ</p>}
      {data && (
        <LineChart
          ariaLabel={`กราฟ${LABELS[series]}เทียบปีที่แล้ว`}
          unit="ล้าน ลบ.ม."
          xTicks={ticks}
          series={years.map((y, i) => ({ label: `ปี ${y.year + 543}`, color: colors[i + (2 - years.length)] ?? "#2f7fb5", points: toPoints(y.values), width: i === years.length - 1 ? 2.4 : 1.6 }))}
          band={hasBand ? { lower: toPoints(data.lower_rule_curve), upper: toPoints(data.upper_rule_curve), color: "#0f766e", label: "เกณฑ์ควบคุมน้ำ (Rule curve)" } : undefined}
        />
      )}
      {hasBand && <p className="source-note">ช่วงสีเขียวคือเกณฑ์ที่กรมชลประทานใช้คุมน้ำในอ่าง ถ้าน้ำสูงกว่าขอบบน เขื่อนมักต้องเพิ่มการระบาย</p>}
    </div>
  );
}
