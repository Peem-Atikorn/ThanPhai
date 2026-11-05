"use client";

import { Assessment, itemTh, levelInfo, MapLayerId, OfficialWarning } from "@/lib/api";
import { areaWarnings } from "@/lib/area-warnings";
import { getLocale, t as tr } from "@/lib/i18n";
import Icon from "@/components/Icon";

function impactDates(from: string | null, to: string | null) {
  const day = (value: string) => new Date(value + "T00:00:00+07:00").toLocaleDateString(getLocale(), {
    day: "numeric", month: "short", year: "numeric", timeZone: "Asia/Bangkok",
  });
  if (from && to) return from === to ? day(from) : `${day(from)} – ${day(to)}`;
  return from ? `${tr("เริ่ม ")}${day(from)}` : to ? `${tr("ถึง ")}${day(to)}` : null;
}

export default function AreaWarnings({ warnings, code, hazard, assessments }: {
  warnings: OfficialWarning[]; code: string; hazard: MapLayerId; assessments: Assessment[];
}) {
  const relevant = areaWarnings(warnings, code, hazard);
  if (!relevant.length) return null;
  return <div className="official-warning area-warnings">
    <strong><Icon name="info"/>{tr("ประกาศทางการ · กรมอุตุนิยมวิทยา")}</strong>
    <p>{tr("ประกาศที่เกี่ยวข้องกับพื้นที่นี้")}</p>
    <ul>{relevant.map(({ warning, impacts }, index) => <li key={`${warning.title}-${index}`}>
      {warning.url ? <a href={warning.url} target="_blank" rel="noreferrer noopener">{warning.title}</a> : warning.title}
      <ul className="area-warning-periods">{impacts.map(({ hazard: h, from, to }, i) => {
        const assessment = assessments.find(a => a.hazard === h);
        const dates = impactDates(from, to);
        return <li key={`${h}-${from}-${to}-${i}`}>
          <b>{tr(itemTh(h))}</b>
          {dates && <span>{tr("ช่วงที่ประกาศระบุ: ")}{dates}</span>}
          {assessment && <span className="warning-assessment">{tr("ระดับประเมินพื้นที่: ")}
            <span className={"risk-badge level-" + (assessment.level ?? "NONE")}>{tr(levelInfo(assessment.level).th)}</span>
          </span>}
          {assessment?.rule_ids.includes("OFFICIAL_WARNING_UPCOMING") && <span>{tr("มีประกาศเตือนล่วงหน้า")}</span>}
        </li>;
      })}</ul>
    </li>)}</ul>
    <small>{tr("ช่วงวันที่มาจากประกาศ ระดับประเมินพื้นที่มาจากข้อมูลล่าสุดของทันภัย")}</small>
  </div>;
}
