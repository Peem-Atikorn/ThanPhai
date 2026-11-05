"use client";

import { useEffect, useRef, useState } from "react";
import type { RadarFrame } from "@/lib/api";
import { CurrentTime } from "@/components/DataClock";
import { getLocale, t as tr } from "@/lib/i18n";

// Keep the original 12-second interval at 1x.
const PLAYBACK_INTERVAL_MS = 12_000;
const PLAYBACK_SPEEDS = [0.2, 0.25, 0.5, 0.75, 1, 1.5, 2, 3, 4, 5];

export default function RadarPlayback({ frames, index, explanation, explanationSource, explanationPending = false, onChange, onPlaybackChange }: {
  frames: RadarFrame[];
  index: number;
  explanation: string;
  explanationSource: "qwen" | "template";
  explanationPending?: boolean;
  onChange: (index: number) => void;
  onPlaybackChange?: (playing: boolean) => void;
}) {
  const [playing, setPlaying] = useState(false);
  const [speedIndex, setSpeedIndex] = useState(4);
  const speed = PLAYBACK_SPEEDS[speedIndex];
  const [position, setPosition] = useState(index);
  const cursor = useRef(index);
  const selected = useRef(index);
  const playback = useRef({ length: frames.length, onChange });
  const frame = frames[index];

  useEffect(() => {
    playback.current = { length: frames.length, onChange };
  }, [frames.length, onChange]);
  useEffect(() => {
    if (selected.current !== index || cursor.current >= frames.length) {
      cursor.current = index;
      selected.current = index;
      setPosition(index);
    }
  }, [index, frames.length]);
  useEffect(() => {
    if (!playing || frames.length < 2) return;
    let previous: number | null = null;
    let animationId: number;
    const tick = (now: number) => {
      const elapsed = previous === null ? 0 : now - previous;
      previous = now;
      const { length, onChange } = playback.current;
      cursor.current = (cursor.current + elapsed * speed / PLAYBACK_INTERVAL_MS) % length;
      setPosition(Math.min(cursor.current, length - 1));
      // The map still receives only whole image changes; fractional progress stays in this component.
      const nextIndex = Math.floor(cursor.current);
      if (nextIndex !== selected.current) {
        selected.current = nextIndex;
        onChange(nextIndex);
      }
      animationId = window.requestAnimationFrame(tick);
    };
    animationId = window.requestAnimationFrame(tick);
    const suspend = () => { previous = null; };
    document.addEventListener("visibilitychange", suspend);
    return () => {
      window.cancelAnimationFrame(animationId);
      document.removeEventListener("visibilitychange", suspend);
    };
  }, [frames.length, playing, speed]);
  useEffect(() => onPlaybackChange?.(playing && frames.length > 1), [frames.length, onPlaybackChange, playing]);

  if (!frame) return null;
  const activePlaying = playing && frames.length > 1;
  const forecast = frame.kind === "forecast";
  const displayPosition = Math.max(0, Math.min(position, frames.length - 1));
  const progress = frames.length > 1 ? displayPosition / (frames.length - 1) : 0;
  const before = Math.floor(displayPosition);
  const after = Math.min(before + 1, frames.length - 1);
  const cursorTime = frames[before].time + (frames[after].time - frames[before].time) * (displayPosition - before);
  const sliderTime = new Intl.DateTimeFormat(getLocale(), { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Bangkok" }).format(cursorTime * 1000);
  const seek = (value: number) => {
    const next = Math.max(0, Math.min(value, frames.length - 1));
    setPlaying(false);
    cursor.current = next;
    selected.current = Math.round(next);
    setPosition(next);
    onChange(selected.current);
  };
  const latestObservedIndex = frames.reduce((latest, frame, i) =>
    frame.kind !== "forecast" && (latest < 0 || frame.time > frames[latest].time) ? i : latest, -1);

  return <div className="radar-playback" role="group" aria-label={tr("เล่นภาพเรดาร์ย้อนหลังและค่าคาดการณ์หนึ่งชั่วโมง")}>
    <div className="radar-playback-actions">
    <button type="button" className="radar-play-button" onClick={() => setPlaying(value => !value)} disabled={frames.length < 2} aria-label={tr(activePlaying ? "หยุดเล่นเรดาร์" : "เล่นเรดาร์")}>
      {activePlaying ? "❚❚" : "▶"}<span>{tr(activePlaying ? "หยุด" : "เล่น")}</span>
    </button>
    <button type="button" className="radar-current-button" disabled={latestObservedIndex < 0}
      onClick={() => seek(latestObservedIndex)}>
      {tr("เวลาปัจจุบัน")}
    </button>
    <div className="radar-speed-control" role="group" aria-label={tr("ความเร็วการเล่นเรดาร์")}>
      <button type="button" aria-label={tr("ลดความเร็ว")} title={tr("ลดความเร็ว")} disabled={speedIndex === 0}
        onClick={() => setSpeedIndex(i => Math.max(0, i - 1))}>−</button>
      <output aria-live="polite" aria-label={tr("ความเร็วการเล่นเรดาร์")}>{speed}×</output>
      <button type="button" aria-label={tr("เพิ่มความเร็ว")} title={tr("เพิ่มความเร็ว")} disabled={speedIndex === PLAYBACK_SPEEDS.length - 1}
        onClick={() => setSpeedIndex(i => Math.min(PLAYBACK_SPEEDS.length - 1, i + 1))}>+</button>
    </div>
    </div>
    <div className="radar-timeline">
    <output className="radar-slider-time" aria-hidden="true"
      style={{ left: `clamp(22px, calc(${progress * 100}% + ${8 - progress * 16}px), calc(100% - 22px))` }}>{sliderTime}</output>
    <input type="range" min={0} max={frames.length - 1} step="any" value={displayPosition} aria-label={tr("เลือกเวลาเรดาร์")} aria-valuetext={sliderTime}
      onKeyDown={(event) => {
        const delta = event.key === "ArrowRight" || event.key === "ArrowUp" ? 1 : event.key === "ArrowLeft" || event.key === "ArrowDown" ? -1 : 0;
        if (delta || event.key === "Home" || event.key === "End") {
          event.preventDefault();
          seek(event.key === "Home" ? 0 : event.key === "End" ? frames.length - 1 : Math.round(displayPosition) + delta);
        }
      }}
      onChange={(event) => seek(Number(event.target.value))}/>
    </div>
    <div className="radar-frame-time">
      <span className={"radar-frame-badge" + (forecast ? " is-forecast" : "")}>{forecast ? <>{tr("ค่าคาดการณ์")} +{frame.horizonMin ?? 0} {tr("นาที")}</> : tr("เรดาร์จริง")}</span>
      <CurrentTime/>
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
