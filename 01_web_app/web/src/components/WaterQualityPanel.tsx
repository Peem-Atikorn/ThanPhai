"use client";

import { t as tr, getLocale } from "@/lib/i18n";
import type { WaterQualityStation } from "@/lib/api";
import Icon from "@/components/Icon";
import { riverTime } from "@/components/RiverPanel";
export const qualityKey = (s: WaterQualityStation) => s.id ?? `${s.name_th}:${s.lat}:${s.lon}`;
export const qualityColor = (s: WaterQualityStation) => Object.keys(s.exceeds).length ? "#e07b16" : s.salinity_gl == null && s.do_mgl == null ? "#8a97a3" : "#2f7fb5";
const value = (v: number | null, digits = 2) => v == null ? "—" : v.toLocaleString(getLocale(), { minimumFractionDigits: digits, maximumFractionDigits: digits });
const references = {
    salinity: "https://www.thaiwater.net/uploads/contents/current/YearlyReport2024/salinity.html",
    surface: "https://www.pcd.go.th/wp-content/uploads/2020/06/pcdnew-2020-06-01_08-31-29_805362.pdf",
    raw: "https://rwc.mwa.co.th/page/home/",
};
const parameters: Record<string, string> = { salinity: "ความเค็ม", do: "ออกซิเจนละลายน้ำ" };
export default function WaterQualityPanel({ stations, selected, onSelect, onClose, status, degraded, source, onRetry }: {
    stations: WaterQualityStation[];
    selected: string | null;
    onSelect: (s: WaterQualityStation) => void;
    onClose: () => void;
    status: "loading" | "error" | "success";
    degraded: boolean;
    source: string;
    onRetry: () => void;
}) {
    const station = stations.find(s => qualityKey(s) === selected);
    const metrics = station ? [
        ["ความเค็ม", station.salinity_gl, "กรัม/ลิตร", "บอกปริมาณเกลือในน้ำ ใช้ติดตามน้ำเค็มรุกและความเหมาะสมของน้ำดิบ", "ThaiWater: เริ่มเฝ้าระวังเมื่อ > 0.25 กรัม/ลิตร; > 0.50 เกินเกณฑ์น้ำดิบผลิตประปา; > 2.0 เกินเกณฑ์เพื่อการเกษตร", references.salinity], ["ออกซิเจนละลายน้ำ", station.do_mgl, "มก./ลิตร", "ออกซิเจนที่ละลายอยู่ในน้ำและสิ่งมีชีวิตใช้หายใจ ค่าต่ำอาจบ่งชี้ปัญหาคุณภาพน้ำ", "น้ำผิวดิน: ประเภท 2 ≥ 6, ประเภท 3 ≥ 4, ประเภท 4 ≥ 2 มก./ลิตร; สัญญาณเตือนของ ThaiWater ในหน้านี้ใช้ < 3 มก./ลิตร", references.surface],
        ["pH", station.ph, "", "บอกความเป็นกรด–ด่าง: 7 เป็นกลาง ต่ำกว่า 7 เป็นกรด สูงกว่า 7 เป็นด่าง", "น้ำผิวดินประเภท 2–4: pH 5–9 ไม่ใช่เกณฑ์รับรองน้ำดื่ม", references.surface], ["ความขุ่น", station.turbidity_ntu, "NTU", "บอกความใส–ขุ่นจากอนุภาคแขวนลอย เช่น ดินหรือตะกอน ค่ายิ่งสูงน้ำยิ่งขุ่น", "ต้องเลือกเกณฑ์ตามประเภทน้ำและการใช้งาน หน้านี้จึงไม่ใช้ค่า NTU เพียงค่าเดียวตัดสินว่าน้ำผ่านมาตรฐาน", references.raw],
        ["ของแข็งละลายน้ำ (TDS)", station.tds_mgl, "มก./ลิตร", "ปริมาณสารที่ละลายในน้ำรวมกัน เช่น เกลือและแร่ธาตุ ไม่ใช่ปริมาณตะกอนที่มองเห็น", "ไม่มีค่าเดียวที่ใช้กับน้ำดิบทุกแหล่ง ต้องเทียบกับประเภทน้ำและวัตถุประสงค์การใช้", references.raw], ["การนำไฟฟ้า", station.conductivity_uscm, "µS/cm", "สะท้อนปริมาณไอออนที่ละลายในน้ำ ใช้ดูแนวโน้มเกลือหรือแร่ธาตุร่วมกับความเค็ม", "กปน. ใช้ < 500 µS/cm เป็นเกณฑ์เฝ้าระวังน้ำดิบที่สำแล เป็นเกณฑ์เฉพาะพื้นที่ ไม่ใช่เส้นตัดสำหรับทุกสถานี", references.raw],
        ["อุณหภูมิ", station.temp_c, "°C", "อุณหภูมิของน้ำขณะตรวจวัด มีผลต่อปริมาณออกซิเจนที่ละลายได้", "น้ำผิวดิน: ไม่สูงกว่าค่าตามธรรมชาติเกิน 3°C ต้องมีค่าอุณหภูมิพื้นฐานเพื่อเทียบ จึงไม่ตัดสินจากตัวเลขปัจจุบันอย่างเดียว", references.surface],
    ] as const : [];
    return <>
    <div className="area-intro"><h2>{tr("คุณภาพน้ำ")}</h2><p className="section-description">{tr("เลือกสถานีเพื่อดูค่าที่วัดได้ เรียงความเค็มมากไปน้อย")}</p></div>
    {status === "loading" && <p role="status" className="search-feedback">{tr("กำลังโหลดข้อมูลคุณภาพน้ำ…")}</p>}
    {status === "error" && <div role="alert" className="data-notice"><p>{tr("โหลดข้อมูลคุณภาพน้ำไม่สำเร็จ")}</p><button type="button" className="text-button" onClick={onRetry}>{tr("ลองใหม่")}</button></div>}
    {(degraded || status === "error" && stations.length > 0) && <p role="status" className="data-notice">{tr("แหล่งข้อมูลคุณภาพน้ำไม่พร้อมใช้งาน อาจเป็นข้อมูลที่เก็บไว้ก่อนหน้า")}</p>}
    {status === "success" && stations.length === 0 && <p role="status" className="search-feedback">{tr("ยังไม่มีข้อมูลคุณภาพน้ำล่าสุด")}</p>}
    {station && <div className="quality-detail">
      <div className="area-title"><span className="area-icon" style={{ color: qualityColor(station) }}><Icon name="river"/></span><div><h3>{station.name_th ?? tr("สถานีวัดคุณภาพน้ำ")}</h3><p>{[station.river_th, station.amphoe_th && tr("อ.") + " " + station.amphoe_th, station.province_th && tr("จ.") + " " + station.province_th].filter(Boolean).join(" · ")}</p></div><button type="button" className="icon-button" aria-label={tr("ปิดรายละเอียดคุณภาพน้ำ")} onClick={onClose}><Icon name="close"/></button></div>
      <p className="quality-guide-label">{tr("แตะแต่ละค่าเพื่อดูความหมายและเกณฑ์อ้างอิง")}</p>
      <ul className="dam-flows quality-metrics">{metrics.map(([label, v, unit, description, reference, url]) => <li key={label}><details><summary><span>{tr(label)}<Icon name="info"/></span><strong>{value(v)}<small> {unit ? tr(unit) : null}</small></strong></summary><div className="quality-help"><p>{tr(description)}</p><p><b>{tr("ค่าอ้างอิง")}</b> · {tr(reference)}</p><a href={url} target="_blank" rel="noreferrer noopener">{tr("อ่านแหล่งอ้างอิง")}<Icon name="arrow"/></a></div></details></li>)}</ul>
      {Object.entries(station.exceeds).map(([key, band]) => <p className="data-notice" key={key}>{tr("เกินเกณฑ์ ThaiWater: ")}{tr(parameters[key] ?? key)} {band.text}</p>)}
      <p className="source-note">{tr("ตรวจวัด ")}{riverTime(station.time)}{tr(" (เวลาไทย)")}{station.agency_th && ` · ${station.agency_th}`}</p>
    </div>}
    {stations.length > 0 && <>
      <p className="source-note">{stations.length}{tr(" สถานี · เกินเกณฑ์ที่ตรวจ ")}{stations.filter(s => Object.keys(s.exceeds).length).length}{tr(" สถานี")}</p>
      <ul className="dam-list quality-stations" tabIndex={0} aria-label={tr("รายชื่อสถานีคุณภาพน้ำ")}>
        {stations.map(s => <li key={qualityKey(s)}><button type="button" className={"dam-row quality-row " + (qualityKey(s) === selected ? "is-active" : "")} aria-pressed={qualityKey(s) === selected} onClick={() => onSelect(s)}>
          <span className="dam-name"><strong>{s.name_th ?? tr("สถานีวัดคุณภาพน้ำ")}</strong><small>{[s.river_th, s.province_th && tr("จ.") + " " + s.province_th].filter(Boolean).join(" · ")}</small></span>
          <span className="dam-value"><strong>{value(s.salinity_gl)}</strong><small>{tr("กรัม/ลิตร")}</small></span>
          <span className="quality-status" style={{ color: Object.keys(s.exceeds).length ? "var(--cc-coral, #e07b16)" : s.salinity_gl == null && s.do_mgl == null ? "var(--muted)" : "var(--cc-blue, #2f7fb5)" }}>
            {Object.keys(s.exceeds).length ? <>{tr("เกินเกณฑ์:")} {Object.keys(s.exceeds).map(k => tr(parameters[k] ?? k)).join(", ")}</> : tr(s.salinity_gl == null && s.do_mgl == null ? "ไม่มีค่าความเค็มและออกซิเจน" : "ไม่พบค่าเกินเกณฑ์ที่ตรวจ")}
          </span>
        </button></li>)}
      </ul>
    </>}
    <p className="source-note">{tr("ที่มา: ")}{source || tr("ThaiWater (สสน.) · กรมควบคุมมลพิษ · การประปานครหลวง")}</p>
    <div className="quality-references"><span>{tr("อ้างอิงเกณฑ์:")}</span> <a href={references.salinity} target="_blank" rel="noreferrer noopener">ThaiWater</a> · <a href={references.surface} target="_blank" rel="noreferrer noopener">{tr("กรมควบคุมมลพิษ")}</a> · <a href={references.raw} target="_blank" rel="noreferrer noopener">{tr("การประปานครหลวง")}</a></div>
    <p className="source-note">{tr("ข้อมูลสถานีเป็นน้ำดิบหรือน้ำผิวดิน ไม่ใช่ผลรับรองน้ำดื่ม")}</p>
    <p className="source-note">{tr("ตรวจเกณฑ์เฉพาะความเค็มและออกซิเจนละลายน้ำ ค่าอื่นแสดงตามที่สถานีวัดได้ · — หมายถึงไม่มีข้อมูล")}</p>
  </>;
}
