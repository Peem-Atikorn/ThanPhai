"use client";

import { useEffect, useRef, useState } from "react";
import type { RadarFrame } from "@/lib/api";
import { CurrentTime } from "@/components/DataClock";
import { getLocale, t as tr } from "@/lib/i18n";

// At 1x the timeline moves one frame (10 min of radar) every 12 s; 3x and 5x only move the clock faster.
// RainViewer allows 100 tiles/min/IP, so an image the browser has not loaded yet is shown at most once every
// 12 s (five a minute, as at 1x): playback waits on that frame until then. Images already shown come from the
// browser cache (RainViewer sends max-age=172800), and forecast frames reuse the latest real image, so after
// the first pass 3x and 5x run without waiting.
const PLAYBACK_INTERVAL_MS = 12_000;
const NEW_IMAGE_INTERVAL_MS = 12_000;
const PLAYBACK_SPEEDS = [1, 3, 5];
// The slider and its time label move in small steps; re-rendering on every animation frame (~60/s) adds nothing.
const SLIDER_UPDATE_MS = 100;
const clock = (seconds: number) => new Intl.DateTimeFormat(getLocale(), { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Bangkok" }).format(seconds * 1000);

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
  const [speedIndex, setSpeedIndex] = useState(0);
  const speed = PLAYBACK_SPEEDS[speedIndex];
  const [position, setPosition] = useState(index);
  const cursor = useRef(index);
  const selected = useRef(index);
  const playback = useRef({ frames, onChange });
  // Tile URLs the map has already shown (so the browser has them), and when playback last showed a new one.
  const seenTiles = useRef(new Set<string>());
  const lastNewImage = useRef(-Infinity);
  const frame = frames[index];

  useEffect(() => {
    playback.current = { frames, onChange };
  }, [frames, onChange]);
  useEffect(() => {
    if (frame?.tiles) seenTiles.current.add(frame.tiles);
  }, [frame?.tiles]);
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
    let rendered = -Infinity;
    let animationId: number;
    const tick = (now: number) => {
      // The shown image and the preloaded next one have just been fetched; the first new one waits its turn too.
      // Timed on the animation clock, the same one every later check uses.
      if (lastNewImage.current === -Infinity) lastNewImage.current = now;
      const elapsed = previous === null ? 0 : now - previous;
      previous = now;
      const { frames, onChange } = playback.current;
      const length = frames.length;
      const before = cursor.current;
      cursor.current = (cursor.current + elapsed * speed / PLAYBACK_INTERVAL_MS) % length;
      // The map still receives only whole image changes; fractional progress stays in this component.
      const nextIndex = Math.floor(cursor.current);
      let changed = nextIndex !== selected.current;
      const tiles = frames[nextIndex]?.tiles;
      if (changed && tiles && !seenTiles.current.has(tiles)) {
        if (now - lastNewImage.current < NEW_IMAGE_INTERVAL_MS) {
          // Not loaded yet and the last new image was too recent: hold at the end of the current frame.
          cursor.current = before;
          changed = false;
        } else {
          lastNewImage.current = now;
          seenTiles.current.add(tiles);
        }
      }
      if (changed || now - rendered >= SLIDER_UPDATE_MS) {
        rendered = now;
        setPosition(Math.min(cursor.current, length - 1));
      }
      if (changed) {
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
  const sliderTime = clock(cursorTime);
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
      {/* The time of the image on the map; the clock beside it is only "now", so the two are labelled apart. */}
      <time className="radar-image-time" dateTime={new Date(frame.time * 1000).toISOString()}>{tr(forecast ? "เวลา " : "ภาพเวลา ")}{clock(frame.time)}{tr(" น.")}</time>
      <small className="radar-now">{tr("ตอนนี้ ")}<CurrentTime/></small>
    </div>
    {forecast && <div className="radar-forecast-copy">
      {frame.imageTime != null && <p>{tr("ภาพคาดการณ์คือภาพเรดาร์จริงเวลา ")}{clock(frame.imageTime)}{tr(" น. เลื่อนตามทิศทางและความเร็วที่วัดได้ ไม่รวมฝนที่เกิดใหม่หรือสลายไป")}</p>}
      {explanationPending && <small>{tr("AI กำลังเรียบเรียงคำอธิบาย ระหว่างนี้แสดงข้อความแม่แบบ")}</small>}
      {/* Shown exactly as validated; translating it would rewrite LLM text after validate(). */}
      {explanation && <p>{explanation}</p>}
      <small>{tr("ค่าคาดการณ์จากการคำนวณทิศทางการเคลื่อนที่ของกลุ่มฝน")}{tr(explanationSource === "qwen" ? " · อธิบายโดย AI ที่รันในเครื่องของทันภัย" : " · ใช้ข้อความแม่แบบจากผลคำนวณ")}{tr(" · ไม่ทำนายฝนที่เกิดใหม่")}</small>
    </div>}
  </div>;
}
