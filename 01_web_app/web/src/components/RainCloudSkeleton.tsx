"use client";

import { t as tr } from "@/lib/i18n";

export default function RainCloudSkeleton({ inline = false, onContinue }: {
    inline?: boolean;
    onContinue?: () => void;
}) {
    return <div className={"rain-skeleton " + (inline ? "rain-skeleton--inline" : "rain-skeleton--page")} role="status" aria-label={tr(inline ? "กำลังโหลดแผนที่" : "กำลังเตรียมข้อมูลทันภัย")}>
    {tr(!inline && <div className="rain-skeleton-layout" aria-hidden="true"><div className="rain-skeleton-header"><i /><i /><i /><i /></div><div className="rain-skeleton-body"><i /><i /><div><i /><i /><i /></div></div></div>)}
    <div className="rain-skeleton-message">
      <svg className="rain-cloud" viewBox="0 0 180 160" fill="none" aria-hidden="true">
        <ellipse cx="90" cy="146" rx="49" ry="5" fill="currentColor" opacity=".08"/>
        <g className="rain-cloud-shape">
          <path d="M45 96a25 25 0 0 1-2-50 33 33 0 0 1 62-13 25 25 0 0 1 36 22 21 21 0 0 1-3 41H45Z" fill="#3d484d" stroke="currentColor" strokeWidth="2"/>
          <path d="M51 47a26 26 0 0 1 45-9" stroke="#d9e3d9" strokeWidth="3" strokeLinecap="round" opacity=".3"/>
          <circle cx="73" cy="70" r="3" fill="#e9eee5"/><circle cx="106" cy="70" r="3" fill="#e9eee5"/>
          <path d="M83 80q7 7 14 0" stroke="#e9eee5" strokeWidth="2.5" strokeLinecap="round"/>
          <ellipse cx="65" cy="79" rx="6" ry="3" fill="currentColor" opacity=".15"/><ellipse cx="114" cy="79" rx="6" ry="3" fill="currentColor" opacity=".15"/>
        </g>
        <g className="rain-cloud-drops" stroke="currentColor" strokeWidth="3" strokeLinecap="round"><path d="m60 108-4 9"/><path d="m91 115-4 9"/><path d="m122 108-4 9"/></g>
      </svg>
      <strong>{tr(inline ? "กำลังเปิดแผนที่" : "กำลังเตรียมข้อมูลทันภัย")}</strong>
      <p>{tr("อีกสักครู่ พร้อมดูแลพื้นที่ของคุณ")}</p>
      {tr(onContinue && <button type="button" onClick={onContinue}>{tr("เปิดหน้าเว็บระหว่างรอ")}</button>)}
    </div>
  </div>;
}
