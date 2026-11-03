"use client";

import { t as tr, getLocale } from "@/lib/i18n";

import Icon from "@/components/Icon";
import { useEffect, useRef } from "react";
import { describeObservation, itemTh, levelInfo, type AreaHazards } from "@/lib/api";
import type { RiskArea } from "@/lib/risk-overview";
import type { RiskFilter } from "@/components/RiskSnapshot";
export default function RiskAlerts({ level, areas, loading, error, updated, selected, area, areaStatus, onSelect, onClear, onRetry }: {
    level: RiskFilter;
    areas: RiskArea[];
    loading: boolean;
    error: boolean;
    updated: string;
    selected: string | null;
    area: AreaHazards | null;
    areaStatus: string;
    onSelect: (code: string) => void;
    onClear: () => void;
    onRetry: () => void;
}) {
    const info = levelInfo(level);
    const panel = useRef<HTMLElement>(null);
    useEffect(() => { if (selected)
        panel.current?.querySelector('[aria-expanded="true"]')?.scrollIntoView({ block: "nearest" }); }, [selected]);
    return <section ref={panel} className="area-panel risk-alert-panel" aria-label={tr(tr("") + tr(info.th) + tr("ล่าสุด"))}>
    <div className="risk-alert-heading"><div><span className="risk-alert-eyebrow">{tr("ภาพรวมทั่วประเทศ")}</span><h2><span style={{ background: info.color }}/>{tr(info.th)}{tr("ล่าสุด")}</h2></div><button className="text-button" type="button" onClick={onClear}>{tr("ดูทุกระดับ")}</button></div>
    <p className="section-description">{tr(areas.length.toLocaleString(getLocale()))}{tr(" อำเภอ \u00B7 ระดับสูงสุดรายอำเภอ")}</p>
    <p className="source-note">{tr("ข้อมูล ณ ")}{tr(updated)}{tr(" (เวลาไทย)")}</p>
    {tr(loading && <p role="status" className="search-feedback">{tr("กำลังอัปเดตข้อมูลพื้นที่\u2026")}</p>)}
    {tr(error && <div role="alert" className="data-notice"><p>{tr("โหลดข้อมูลล่าสุดไม่สำเร็จ")}{tr(areas.length > 0 && " · แสดงข้อมูลเดิม")}</p><button className="text-button" type="button" onClick={onRetry}>{tr("ลองใหม่")}</button></div>)}
    {tr(!loading && !error && !areas.length && <p className="search-feedback">{tr("ยังไม่มีอำเภอระดับ")}{tr(info.th)}{tr("ในข้อมูลชุดล่าสุด")}</p>)}
    <ul className="risk-alert-list">{tr(areas.map(item => <li key={item.code}>
      <button type="button" className={"risk-alert-row" + (selected === item.code ? " is-active" : "")} aria-expanded={selected === item.code} onClick={() => onSelect(item.code)}>
        <span className="risk-alert-icon" style={{ color: info.color }}><Icon name={item.hazard ?? "info"}/></span>
        <span><strong>{tr(item.hazard ? itemTh(item.hazard) : "ไม่ระบุประเภทภัย")}</strong><span>{tr(item.province === "กรุงเทพมหานคร" ? "เขต" : "อ.")}{tr(item.name)}</span><small>{tr(item.province)}{tr(item.hazards > 1 && tr(" \u00B7 มีภัยเฝ้าระวังขึ้นไป ") + tr(item.hazards) + tr(" ประเภท"))}</small></span><Icon name="arrow"/>
      </button>
      {tr(selected === item.code && <div className="risk-alert-detail">
        {tr(areaStatus === "loading" ? <p role="status">{tr("กำลังอ่านรายละเอียดพื้นที่\u2026")}</p> : areaStatus === "error" ? <button type="button" className="text-button" onClick={() => onSelect(item.code)}>{tr("โหลดรายละเอียดใหม่")}</button> : area?.area.code === item.code && <>
          {tr(area.assessments.filter(a => a.level === level).map(a => <div key={a.hazard}><strong>{tr(itemTh(a.hazard))}</strong><p>{tr(Object.entries(a.observations).map(([key, value]) => describeObservation(key, value)).filter(Boolean).join(" · ") || "ระดับจากผลประเมินล่าสุดของระบบ")}</p>{tr(a.notices.map(n => <p key={n}>{tr(n)}</p>))}<small>{tr("ที่มา: ")}{tr(a.sources.join(", ") || "ยังไม่ระบุแหล่งข้อมูล")}</small></div>))}
          {tr(!area.assessments.some(a => a.level === level) && <p>{tr("ผลประเมินพื้นที่อาจอัปเดตแล้ว โปรดโหลดภาพรวมใหม่เพื่อตรวจระดับล่าสุด")}</p>)}
        </>)}
      </div>)}
    </li>))}</ul>
    {tr(!!areas.length && <p className="source-note">{tr("เรียงตามจังหวัดและชื่ออำเภอ \u00B7 จุดบนแผนที่แทนตำแหน่งอำเภอ ไม่ใช่พิกัดเหตุเฉพาะจุด")}</p>)}
  </section>;
}
