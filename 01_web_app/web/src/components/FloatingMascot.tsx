"use client";

import { type CSSProperties, type KeyboardEvent, type PointerEvent, type ReactNode, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Icon from "@/components/Icon";
import { t as tr } from "@/lib/i18n";
import type { MapItem } from "@/lib/api";
import { clampMascotPosition, MASCOT_SIZE, MASCOT_TOPICS, placeMascotChat, readMascotViewport, type MascotPosition, type MascotViewport } from "@/lib/mascot";

type Status = "idle" | "waiting" | "answered" | "error";
type Gesture = "idle" | "wave" | "happy";

export default function FloatingMascot({ topic, status, children }: { topic: MapItem; status: Status; children: ReactNode }) {
  const [viewport, setViewport] = useState<MascotViewport | null>(null);
  const [position, setPosition] = useState<MascotPosition>({ x: 8, y: 8 });
  const [open, setOpen] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [gesture, setGesture] = useState<Gesture>("idle");
  const [frame, setFrame] = useState(0);
  const [panelHeight, setPanelHeight] = useState(360);
  const button = useRef<HTMLButtonElement>(null);
  const home = useRef<HTMLDivElement>(null);
  const displaced = useRef(false);
  const panel = useRef<HTMLDivElement>(null);
  const drag = useRef<{ id: number; startX: number; startY: number; origin: MascotPosition; moved: boolean } | null>(null);
  const suppressClick = useRef(false);
  const activePosition = useRef(position);
  const info = MASCOT_TOPICS[topic];

  function move(next: MascotPosition) {
    if (!viewport) return;
    const bounded = clampMascotPosition(next, viewport);
    displaced.current = true;
    activePosition.current = bounded;
    setPosition(bounded);
  }

  useEffect(() => {
    function homePosition(bounds: MascotViewport) {
      const rect = home.current?.getBoundingClientRect();
      return clampMascotPosition(rect ? {
        x: rect.left + (rect.width - MASCOT_SIZE.width) / 2 - (bounds.offsetLeft ?? 0),
        y: rect.top + rect.height - MASCOT_SIZE.height - 16 - (bounds.offsetTop ?? 0),
      } : null, bounds);
    }
    const initial = readMascotViewport(window);
    const next = homePosition(initial);
    activePosition.current = next;
    setPosition(next);
    setViewport(initial);
    function resize() {
      const bounds = readMascotViewport(window);
      const nextPosition = displaced.current ? clampMascotPosition(activePosition.current, bounds) : homePosition(bounds);
      activePosition.current = nextPosition;
      setPosition(nextPosition);
      setViewport(bounds);
    }
    window.addEventListener("resize", resize);
    window.addEventListener("scroll", resize, true);
    window.visualViewport?.addEventListener("resize", resize);
    window.visualViewport?.addEventListener("scroll", resize);
    const observer = new ResizeObserver(resize);
    if (home.current) observer.observe(home.current);
    return () => {
      window.removeEventListener("resize", resize);
      window.removeEventListener("scroll", resize, true);
      observer.disconnect();
      window.visualViewport?.removeEventListener("resize", resize);
      window.visualViewport?.removeEventListener("scroll", resize);
    };
  }, []);

  useEffect(() => {
    const start = window.requestAnimationFrame(() => setGesture("wave"));
    const timeout = window.setTimeout(() => setGesture("idle"), 1400);
    return () => { window.cancelAnimationFrame(start); window.clearTimeout(timeout); };
  }, [topic, open]);

  useEffect(() => {
    if (status !== "answered") return;
    const start = window.requestAnimationFrame(() => setGesture("happy"));
    const timeout = window.setTimeout(() => setGesture("idle"), 1500);
    return () => { window.cancelAnimationFrame(start); window.clearTimeout(timeout); };
  }, [status]);

  useEffect(() => {
    if (!open || !panel.current) return;
    const content = panel.current;
    // Natural height: the panel itself never overflows (only the chat log scrolls), so add what the log hides.
    const measure = () => {
      const log = content.querySelector<HTMLElement>(".chat-log");
      setPanelHeight(content.scrollHeight + (log ? log.scrollHeight - log.clientHeight : 0) + 2);
    };
    const observer = new ResizeObserver(measure);
    observer.observe(content);
    // New messages grow the log's content, not the panel's box, so watch the content too.
    const mutations = new MutationObserver(measure);
    mutations.observe(content, { childList: true, subtree: true, characterData: true });
    measure();
    const focusTimer = window.setTimeout(() => content.querySelector<HTMLInputElement>("input")?.focus({ preventScroll: true }), 160);
    function escape(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        button.current?.focus({ preventScroll: true });
      }
    }
    document.addEventListener("keydown", escape);
    return () => {
      observer.disconnect();
      mutations.disconnect();
      window.clearTimeout(focusTimer);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  const phase = dragging ? "drag" : status === "waiting" ? "think" : status === "error" ? "error" : gesture;
  const original = topic === "overview";
  const row = phase === "drag" ? 1 : phase === "think" ? 6 : phase === "wave" ? 3 : phase === "happy" ? 4 : phase === "error" ? 5 : status === "answered" ? 8 : 0;
  const counts = [6, 8, 8, 4, 5, 8, 6, 6, 6];

  useEffect(() => {
    const reset = window.requestAnimationFrame(() => setFrame(0));
    if (!original || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return () => window.cancelAnimationFrame(reset);
    const timer = window.setInterval(() => setFrame(value => (value + 1) % counts[row]), 150);
    return () => { window.cancelAnimationFrame(reset); window.clearInterval(timer); };
  // Frame counts are the fixed layout of the supplied v2 atlas.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [original, row]);

  function pointerDown(event: PointerEvent<HTMLButtonElement>) {
    if (!event.isPrimary || event.button !== 0) return;
    suppressClick.current = false;
    drag.current = { id: event.pointerId, startX: event.clientX, startY: event.clientY, origin: activePosition.current, moved: false };
    event.currentTarget.setPointerCapture(event.pointerId);
  }

  function pointerMove(event: PointerEvent<HTMLButtonElement>) {
    const pending = drag.current;
    if (!pending || pending.id !== event.pointerId) return;
    const dx = event.clientX - pending.startX;
    const dy = event.clientY - pending.startY;
    if (!pending.moved && Math.hypot(dx, dy) < 6) return;
    pending.moved = true;
    suppressClick.current = true;
    setDragging(true);
    move({ x: pending.origin.x + dx, y: pending.origin.y + dy });
  }

  function pointerEnd(event: PointerEvent<HTMLButtonElement>) {
    const pending = drag.current;
    if (!pending || pending.id !== event.pointerId) return;
    drag.current = null;
    setDragging(false);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  }

  function keyMove(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === "Enter" || event.key === " ") suppressClick.current = false;
    if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
    event.preventDefault();
    const step = event.shiftKey ? 40 : 12;
    move({ x: position.x + (event.key === "ArrowRight" ? step : event.key === "ArrowLeft" ? -step : 0), y: position.y + (event.key === "ArrowDown" ? step : event.key === "ArrowUp" ? -step : 0) });
  }

  if (!viewport) return <><div ref={home} className="mascot-home" aria-hidden="true" /></>;
  const placement = placeMascotChat(position, viewport, panelHeight);
  const stripFrame = phase === "think" || phase === "error" ? 2 : phase === "happy" ? 3 : phase === "wave" || phase === "drag" ? 1 : 0;
  const spriteStyle: CSSProperties = {
    backgroundImage: `url("${info.src}")`,
    backgroundSize: original ? "800% 1100%" : "400% 100%",
    backgroundPosition: original ? `${(Math.min(frame, counts[row] - 1) / 7) * 100}% ${(row / 10) * 100}%` : `${(stripFrame / 3) * 100}% 50%`,
  };

  return <><div ref={home} className="mascot-home" aria-hidden="true" />{createPortal(<div className="thanphai-mascot" data-topic={topic} data-phase={phase}>
    <button ref={button} type="button" className="mascot-character" style={{ left: position.x + (viewport.offsetLeft ?? 0), top: position.y + (viewport.offsetTop ?? 0) }}
      aria-label={`${tr(info.label)} · ${tr(open ? "ปิดแชท" : "เปิดแชท")}`} aria-expanded={open} aria-controls="thanphai-mascot-chat" aria-describedby="thanphai-mascot-help"
      onPointerDown={pointerDown} onPointerMove={pointerMove} onPointerUp={pointerEnd} onPointerCancel={pointerEnd} onLostPointerCapture={pointerEnd} onKeyDown={keyMove}
      onClick={() => { if (suppressClick.current) { suppressClick.current = false; return; } setOpen(value => !value); }}>
      {!open && <span className="mascot-invitation">{tr("ถามทันภัย")}</span>}
      <span className={"mascot-sprite" + (original ? " mascot-sprite--original" : "")} style={spriteStyle} aria-hidden="true" />
      <span className="mascot-ground" aria-hidden="true" />
    </button>
    <span id="thanphai-mascot-help" className="mascot-sr-only">{tr("ลากเพื่อย้าย กดเพื่อคุย หรือใช้ปุ่มลูกศรเพื่อย้ายตำแหน่ง")}</span>
    <div ref={panel} id="thanphai-mascot-chat" className="mascot-chat" role="dialog" aria-modal="false" aria-labelledby="thanphai-mascot-title" hidden={!open}
      style={{ left: placement.left + (viewport.offsetLeft ?? 0), top: placement.top + (viewport.offsetTop ?? 0), width: placement.width, maxHeight: placement.maxHeight }}>
      <div className="mascot-chat-heading">
        <span className="mascot-chat-icon"><Icon name="chat" /></span>
        <div><h2 id="thanphai-mascot-title">{tr("ถามทันภัย")}</h2><p>{tr("ช่วยอ่านข้อมูลความเสี่ยงให้เข้าใจง่าย")}</p></div>
        <button type="button" className="mascot-close" aria-label={tr("ย่อแชท")} onClick={() => { setOpen(false); button.current?.focus({ preventScroll: true }); }}>−</button>
      </div>
      {children}
    </div>
  </div>, document.body)}</>;
}
