"use client";

import { useEffect, useRef, useState } from "react";
import type { RadarFrame } from "@/lib/api";
import { getLocale, t as tr } from "@/lib/i18n";

// Five frame changes per minute leave room for the initial and preloaded images within RainViewer's 100 tiles/min/IP limit.
const PLAYBACK_INTERVAL_MS = 12_000;

export default function RadarPlayback({ frames, index, onChange, onPlaybackChange }: {
  frames: RadarFrame[];
  index: number;
  onChange: (index: number) => void;
  onPlaybackChange: (playing: boolean) => void;
}) {
  const [playing, setPlaying] = useState(false);
  const playback = useRef({ index, length: frames.length, onChange });
  playback.current = { index, length: frames.length, onChange };
  const frame = frames[index];

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

  return <div className="radar-playback" role="group" aria-label={tr("เล่นภาพเรดาร์ย้อนหลังหนึ่งชั่วโมง")}>
    <button type="button" className="radar-play-button" onClick={() => setPlaying((value) => !value)} disabled={frames.length < 2} aria-label={tr(activePlaying ? "หยุดเล่นเรดาร์" : "เล่นเรดาร์")}>
      {activePlaying ? "❚❚" : "▶"}<span>{tr(activePlaying ? "หยุด" : "เล่น")}</span>
    </button>
    <input type="range" min={0} max={frames.length - 1} step={1} value={index} aria-label={tr("เลือกเวลาเรดาร์")}
      onChange={(event) => { setPlaying(false); onChange(Number(event.target.value)); }}/>
    <time dateTime={new Date(frame.time * 1000).toISOString()}>{time}{tr(" น. เวลาไทย")}</time>
  </div>;
}
