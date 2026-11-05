"use client";

import { t as tr, getLocale } from "@/lib/i18n";

import { useState, type CSSProperties } from "react";
import Icon from "@/components/Icon";
export type RiskFilter = "CRITICAL" | "WARNING" | "WATCH";
type Count = {
    id: string;
    n: number;
    color: string;
    th: string;
};
export default function RiskSnapshot({ total, counts, scope, activeLevel, overviewActive = true, onSelect }: {
    total: number | null;
    counts: Count[];
    scope?: string;
    activeLevel?: RiskFilter | null;
    overviewActive?: boolean;
    onSelect?: (level: RiskFilter | null) => void;
}) {
    const number = new Intl.NumberFormat(getLocale());
    const [hovered, setHovered] = useState<string | null>(null);
    const hoverHandlers = (id: string) => ({
        "data-hovered": hovered === id,
        onPointerEnter: (event: React.PointerEvent<HTMLElement>) => { if (event.pointerType === "mouse" || event.pointerType === "pen")
            setHovered(id); },
        onPointerLeave: () => setHovered(null),
        onPointerCancel: () => setHovered(null),
    });
    return <section className="situation-strip" aria-label={tr("สรุปสถานการณ์" + (scope ? " · " + scope : "บนแผนที่"))}>
    <article className="situation-total" {...hoverHandlers("total")}><button type="button" aria-label={tr("ดูภาพรวมทุกระดับ")} aria-pressed={overviewActive && !activeLevel} onClick={() => onSelect?.(null)}><span className="snapshot-icon"><Icon name="map"/></span><span className="snapshot-label">{tr("อำเภอในแผนที่")}</span><strong>{tr(total == null ? "—" : number.format(total))}</strong><small>{tr(scope ? "ข้อมูล" + scope : "พื้นที่ทั่วประเทศไทย")}</small></button></article>
    {tr((["CRITICAL", "WARNING", "WATCH"] as const).map(id => {
            const item = counts.find(c => c.id === id);
            const label = item?.th ?? ({ CRITICAL: "วิกฤต", WARNING: "เตือนภัย", WATCH: "เฝ้าระวัง" }[id]);
            return <article key={id} {...hoverHandlers(id)} style={{ "--metric-color": item?.color } as CSSProperties}>
        <button type="button" aria-label={tr(tr("ดูพื้นที่ระดับ") + tr(label) + tr(""))} aria-pressed={activeLevel === id} onClick={() => onSelect?.(id)}><span className="snapshot-icon"><Icon name={id === "CRITICAL" ? "shield" : id === "WARNING" ? "info" : "radar"}/></span><span className="snapshot-label">{tr(label)}</span><strong>{tr(total == null ? "—" : number.format(item?.n ?? 0))}</strong><small>{tr("อำเภอ \u00B7 ระดับสูงสุดในพื้นที่")}</small></button>
      </article>;
        }))}
  </section>;
}
