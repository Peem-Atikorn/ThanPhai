"use client";

import { useEffect, useRef, useState } from "react";
import type { RadarFrame } from "@/lib/api";
import { getLocale, t as tr } from "@/lib/i18n";

// Five frame changes per minute leave room for the initial and preloaded images within RainViewer's 100 tiles/min/IP limit.
const PLAYBACK_INTERVAL_MS = 12_000;

export default function RadarPlayback({ frames, index, explanation, explanationSource, explanationPending = false, onChange, onPlaybackChange }: {
  frames: RadarFrame[];
  index: number;
  explanation: string;
  explanationSource: "qwen" | "template";
  explanationPending?: boolean;
  onChange: (index: number) => void;
  onPlaybackChange: (playing: boolean) => void;
}) {
  const [playing, setPlaying] = useState(false);
  const playback = useRef({ index, length: frames.length, onChange });
  const frame = frames[index];

  useEffect(() => {
    playback.current = { index, length: frames.length, onChange };
  }, [frames.length, index, onChange]);
  useEffect(() => {
    if (!playing || frames.length < 2) return;
    const timer = window.setInterval(() => {
      const { index, length, onChange } = playback.current;
      onChange((index + 1) % length);
    }, PLAYBACK_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [frames.length, playing]);
  useEffect(() => onPlaybackChange(playing && frames.length > 1), [frames.length, onPlaybackChange, playing]);

  if (!frame) return null;
  const activePlaying = playing && frames.length > 1;
  const time = new Intl.DateTimeFormat(getLocale(), { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Bangkok" }).format(frame.time * 1000);
  const forecast = frame.kind === "forecast";

  return <div className="radar-playback" role="group" aria-label={tr("เล่นภาพเรดาร์ย้อนหลังและค่าคาดการณ์หนึ่งชั่วโมง")}>
    <button type="button" className="radar-play-button" onClick={() => setPlaying((value) => !value)} disabled={frames.length < 2} aria-label={tr(activePlaying ? "หยุดเล่นเรดาร์" : "เล่นเรดาร์")}>
      {activePlaying ? "❚❚" : "▶"}<span>{tr(activePlaying ? "หยุด" : "เล่น")}</span>
    </button>
    <input type="range" min={0} max={frames.length - 1} step={1} value={index} aria-label={tr("เลือกเวลาเรดาร์")}
      onChange={(event) => { setPlaying(false); onChange(Number(event.target.value)); }}/>
    <div className="radar-frame-time">
      <span className={"radar-frame-badge" + (forecast ? " is-forecast" : "")}>{forecast ? <>{tr("ค่าคาดการณ์")} +{frame.horizonMin ?? 0} {tr("นาที")}</> : tr("เรดาร์จริง")}</span>
      <time dateTime={new Date(frame.time * 1000).toISOString()}>{time}{tr(" น. เวลาไทย")}</time>
    </div>
    {forecast && <div className="radar-forecast-copy">
      {frame.imageTime != null && <p>{tr("ภาพจางคือเรดาร์จริงล่าสุดเวลา ")}{new Intl.DateTimeFormat(getLocale(), { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Bangkok" }).format(frame.imageTime * 1000)}{tr(" น. ไม่ใช่ภาพฝนคาดการณ์")}</p>}
      {explanationPending && <small>{tr("AI กำลังเรียบเรียงคำอธิบาย ระหว่างนี้แสดงข้อความแม่แบบ")}</small>}
      {/* Shown exactly as validated; translating it would rewrite LLM text after validate(). */}
      {explanation && <p>{explanation}</p>}
      <small>{tr("ค่าคาดการณ์จากการคำนวณทิศทางการเคลื่อนที่ของกลุ่มฝน")}{tr(explanationSource === "qwen" ? " · อธิบายโดย AI ที่รันในเครื่องของทันภัย" : " · ใช้ข้อความแม่แบบจากผลคำนวณ")}{tr(" · ไม่ทำนายฝนที่เกิดใหม่")}</small>
    </div>}
  </div>;
}
