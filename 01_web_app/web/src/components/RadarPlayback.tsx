"use client";

import { useEffect, useRef, useState } from "react";
import type { RadarFrame } from "@/lib/api";
import { CurrentTime } from "@/components/DataClock";
import { getLocale, t as tr } from "@/lib/i18n";

// At 1x the timeline moves one frame (10 min of radar) every 12 s; 3x and 5x only move the clock faster.
// RainViewer allows 100 tiles/min/IP, so the browser fetches a new image at most once every 12 s (five a minute).
// From the moment the radar is shown, the map's hidden preload layer fetches the images not yet loaded, one per
// 12 s, starting after the shown one, whether or not playback is running; playback waits only on a frame whose
// image the map has not reported loaded yet, and says so. A failed or stuck fetch is tried again on a later turn;
// after MAX_TRIES playback stops waiting for it. Loaded images come from the browser cache (RainViewer sends
// max-age=172800), and forecast frames reuse the latest real image, so after one pass 3x and 5x run freely.
const PLAYBACK_INTERVAL_MS = 12_000;
const NEW_IMAGE_INTERVAL_MS = 12_000;
const PRELOAD_CHECK_MS = 1_000;
const LOAD_TIMEOUT_MS = 30_000;
const MAX_TRIES = 3;
// Per page, not per mount: switching views and back must not wait again for images the browser already has.
// `loadedTiles` holds what the map reported drawn; `pendingTiles` what it is fetching, and since when.
const loadedTiles = new Set<string>();
const pendingTiles = new Map<string, number>();
const failedTries = new Map<string, number>();
let lastNewImage = -Infinity;
const startFetch = (tiles: string) => {
  pendingTiles.set(tiles, performance.now());
  lastNewImage = performance.now();
};
/** The map has drawn every visible tile of this radar template. */
export function radarImageLoaded(tiles: string) {
  pendingTiles.delete(tiles);
  loadedTiles.add(tiles);
}
/** A tile of this template failed (or took too long): fetch it again later, or stop waiting after MAX_TRIES. */
export function radarImageFailed(tiles: string) {
  // Each failing tile reports; count one try per fetch.
  if (loadedTiles.has(tiles) || !pendingTiles.has(tiles)) return;
  pendingTiles.delete(tiles);
  const tries = (failedTries.get(tiles) ?? 0) + 1;
  failedTries.set(tiles, tries);
  if (tries >= MAX_TRIES) loadedTiles.add(tiles);
}
const PLAYBACK_SPEEDS = [1, 3, 5];
// The slider and its time label move in small steps; re-rendering on every animation frame (~60/s) adds nothing.
const SLIDER_UPDATE_MS = 100;
const clock = (seconds: number) => new Intl.DateTimeFormat(getLocale(), { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Bangkok" }).format(seconds * 1000);

export default function RadarPlayback({ frames, index, explanation, explanationSource, explanationPending = false, onChange, onPreload }: {
  frames: RadarFrame[];
  index: number;
  explanation: string;
  explanationSource: "qwen" | "template";
  explanationPending?: boolean;
  onChange: (index: number) => void;
  /** Tile URL template for the map's hidden preload layer (the next image not yet loaded). */
  onPreload?: (tiles: string | null) => void;
}) {
  const [playing, setPlaying] = useState(false);
  const [speedIndex, setSpeedIndex] = useState(0);
  const speed = PLAYBACK_SPEEDS[speedIndex];
  const [position, setPosition] = useState(index);
  const cursor = useRef(index);
  const selected = useRef(index);
  const playback = useRef({ frames, onChange, onPreload });
  // Playback is holding on a frame whose image has not loaded yet.
  const [waiting, setWaiting] = useState(false);
  const waitingRef = useRef(false);
  const frame = frames[index];

  useEffect(() => {
    playback.current = { frames, onChange, onPreload };
  }, [frames, onChange, onPreload]);
  useEffect(() => {
    // An image shown by picking it on the slider is a new fetch too, and counts against the same budget.
    if (frame?.tiles && !loadedTiles.has(frame.tiles) && !pendingTiles.has(frame.tiles)) startFetch(frame.tiles);
  }, [frame?.tiles]);
  useEffect(() => {
    // Fetch the next image not yet loaded, going forward from the shown frame, when the budget allows.
    // One fetch at a time: giving the preload layer new tiles would cancel the image it is still loading.
    const preload = () => {
      const { frames, onPreload } = playback.current;
      if (!onPreload) return;
      const now = performance.now();
      let stuck = false;
      for (const [tiles, since] of pendingTiles) if (now - since > LOAD_TIMEOUT_MS) { radarImageFailed(tiles); stuck = true; }
      // Clear the preload layer first, so trying the same image again makes the map fetch it anew.
      if (stuck) { onPreload(null); return; }
      if (pendingTiles.size > 0 || now - lastNewImage < NEW_IMAGE_INTERVAL_MS) return;
      for (let step = 1; step < frames.length; step++) {
        const tiles = frames[(selected.current + step) % frames.length]?.tiles;
        if (tiles && !loadedTiles.has(tiles)) {
          startFetch(tiles);
          onPreload(tiles);
          return;
        }
      }
    };
    preload();
    const timer = window.setInterval(preload, PRELOAD_CHECK_MS);
    return () => window.clearInterval(timer);
  }, []);
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
      const hold = changed && !!tiles && !loadedTiles.has(tiles);
      if (hold) {
        // Not loaded yet: hold at the end of the current frame until the map reports the preloaded image.
        cursor.current = before;
        changed = false;
      }
      if (hold !== waitingRef.current) {
        waitingRef.current = hold;
        setWaiting(hold);
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
      waitingRef.current = false;
      setWaiting(false);
    };
  }, [frames.length, playing, speed]);

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
  // Distinct images in the timeline (forecast frames reuse the latest real one) and how many have loaded.
  const images = [...new Set(frames.map(f => f.tiles).filter((t): t is string => !!t))];
  const imagesFetched = images.filter(t => loadedTiles.has(t)).length;
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
    {/* Shown for the whole first pass rather than each pause, so the panel does not jump every few seconds. */}
    {activePlaying && imagesFetched < images.length && <p className={"radar-loading" + (waiting ? " is-waiting" : "")} role="status">
      {tr("กำลังโหลดภาพเรดาร์ ")}{imagesFetched}/{images.length}{tr(" ภาพ · RainViewer ให้โหลดภาพใหม่ได้ทุก 12 วินาที ภาพที่โหลดแล้วจะเล่นต่อเนื่อง")}
    </p>}
    {forecast && <div className="radar-forecast-copy">
      {frame.imageTime != null && <p>{tr("ภาพคาดการณ์คือภาพเรดาร์จริงเวลา ")}{clock(frame.imageTime)}{tr(" น. เลื่อนตามทิศทางและความเร็วที่วัดได้ ไม่รวมฝนที่เกิดใหม่หรือสลายไป")}</p>}
      {explanationPending && <small>{tr("AI กำลังเรียบเรียงคำอธิบาย ระหว่างนี้แสดงข้อความแม่แบบ")}</small>}
      {/* Shown exactly as validated; translating it would rewrite LLM text after validate(). */}
      {explanation && <p>{explanation}</p>}
      <small>{tr("ค่าคาดการณ์จากการคำนวณทิศทางการเคลื่อนที่ของกลุ่มฝน")}{tr(explanationSource === "qwen" ? " · อธิบายโดย AI ที่รันในเครื่องของทันภัย" : " · ใช้ข้อความแม่แบบจากผลคำนวณ")}{tr(" · ไม่ทำนายฝนที่เกิดใหม่")}</small>
    </div>}
  </div>;
}
