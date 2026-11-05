"use client";

import { useSyncExternalStore } from "react";
import { getLocale, t as tr } from "@/lib/i18n";

// The clock ticks every 15 s; the snapshot changes once a minute, so the footer re-renders only when the minute does.
const subscribe = (notify: () => void) => { const id = window.setInterval(notify, 15000); return () => window.clearInterval(id); };
const minute = () => Math.floor(Date.now() / 60000);

function thaiTime(ms: number) {
    return new Date(ms).toLocaleTimeString(getLocale(), { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Bangkok" }) + tr(" น.");
}

/** "ดึงข้อมูลล่าสุด 06:40 น. · ตอนนี้ 06:52 น." so a reader can see how old the map is. */
export default function DataClock({ updatedAt }: { updatedAt: string | null }) {
    const now = useSyncExternalStore(subscribe, minute, () => null);
    const fetched = updatedAt ? Date.parse(updatedAt) : NaN;
    if (now === null) return null;  // no clock on the server render
    // A div, not a p: the landscape footer hides its <p> children.
    return <div className="data-clock" aria-live="off">
        {tr("ดึงข้อมูลล่าสุด ")}{Number.isNaN(fetched) ? "—" : thaiTime(fetched)}{tr(" · ตอนนี้ ")}{thaiTime(now * 60000)}{tr(" (เวลาไทย)")}
    </div>;
}

/** Current Thai time, independent of the selected radar image. */
export function CurrentTime() {
    const now = useSyncExternalStore(subscribe, minute, () => null);
    const time = now === null ? null : new Date(now * 60000);
    return <time dateTime={time?.toISOString()} aria-live="off">
        {time ? time.toLocaleTimeString(getLocale(), { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Bangkok" }) : "—"}{tr(" น. เวลาไทย")}
    </time>;
}
