import type { MapItem } from "./api";

export const MASCOT_SIZE = { width: 120, height: 160 };
export const MASCOT_TOPICS: Record<MapItem, { label: string; src: string }> = {
  overview: { label: "ผู้ช่วยทันภัย", src: "/mascot/overview.webp" },
  flood: { label: "ช้างกู้ภัยน้ำท่วม", src: "/mascot/flood-strip.webp" },
  rain_storm: { label: "ช้างเฝ้าระวังฝนและพายุ", src: "/mascot/rain_storm-strip.webp" },
  fire: { label: "ช้างนักดับไฟป่า", src: "/mascot/fire-strip.webp" },
  drought: { label: "ช้างดูแลภัยแล้ง", src: "/mascot/drought-strip.webp" },
  air_quality: { label: "ช้างตรวจฝุ่น PM2.5", src: "/mascot/air_quality-strip.webp" },
  heat: { label: "ช้างดูแลอากาศร้อน", src: "/mascot/heat-strip.webp" },
  earthquake: { label: "ช้างกู้ภัยแผ่นดินไหว", src: "/mascot/earthquake-strip.webp" },
  marine: { label: "ช้างผู้ช่วยทางทะเล", src: "/mascot/marine-strip.webp" },
  tropical_cyclone: { label: "ช้างเฝ้าระวังพายุหมุน", src: "/mascot/tropical_cyclone-strip.webp" },
  landslide: { label: "ช้างกู้ภัยดินถล่ม", src: "/mascot/landslide-strip.webp" },
  dams: { label: "ช้างวิศวกรเขื่อน", src: "/mascot/dams-strip.webp" },
  rivers: { label: "ช้างสำรวจแม่น้ำ", src: "/mascot/rivers-strip.webp" },
  "water-quality": { label: "ช้างตรวจคุณภาพน้ำ", src: "/mascot/water-quality-strip.webp" },
};

export type MascotPosition = { x: number; y: number };
export type MascotViewport = { width: number; height: number; offsetLeft?: number; offsetTop?: number };

export function readMascotViewport(view: { innerWidth: number; innerHeight: number; visualViewport?: { width: number; height: number; offsetLeft: number; offsetTop: number } | null }): MascotViewport {
  return {
    width: Math.min(view.innerWidth, view.visualViewport?.width ?? view.innerWidth),
    height: Math.min(view.innerHeight, view.visualViewport?.height ?? view.innerHeight),
    offsetLeft: view.visualViewport?.offsetLeft ?? 0,
    offsetTop: view.visualViewport?.offsetTop ?? 0,
  };
}

export function clampMascotPosition(saved: unknown, viewport: MascotViewport): MascotPosition {
  const candidate = saved as Partial<MascotPosition> | null;
  const fallback = { x: 12, y: viewport.height - MASCOT_SIZE.height - (viewport.width <= 480 ? 80 : 60) };
  const x = typeof candidate?.x === "number" && Number.isFinite(candidate.x) ? candidate.x : fallback.x;
  const y = typeof candidate?.y === "number" && Number.isFinite(candidate.y) ? candidate.y : fallback.y;
  return {
    x: Math.max(8, Math.min(x, viewport.width - MASCOT_SIZE.width - 8)),
    y: Math.max(8, Math.min(y, viewport.height - MASCOT_SIZE.height - 8)),
  };
}

export function placeMascotChat(position: MascotPosition, viewport: MascotViewport, contentHeight: number) {
  const margin = 8;
  const gap = 12;
  const width = Math.min(420, viewport.width - margin * 2);
  // Tall enough to read a few answers at once: up to 85% of the screen (at least 420px when the screen allows).
  const height = Math.min(contentHeight, Math.max(420, Math.round(viewport.height * 0.85)), viewport.height - margin * 2);
  const right = position.x + MASCOT_SIZE.width + gap;
  const left = position.x - width - gap;
  if (right + width <= viewport.width - margin || left >= margin) {
    return {
      left: right + width <= viewport.width - margin ? right : left,
      top: Math.max(margin, Math.min(position.y + MASCOT_SIZE.height - height, viewport.height - height - margin)),
      width,
      maxHeight: height,
    };
  }
  const roomAbove = Math.max(0, position.y - gap - margin);
  const roomBelow = Math.max(0, viewport.height - position.y - MASCOT_SIZE.height - gap - margin);
  // shortcut: on very short screens the panel overlays the avatar; Escape and the close button stay reachable.
  const maxHeight = Math.max(roomAbove, roomBelow) >= 220 ? Math.min(height, Math.max(roomAbove, roomBelow)) : height;
  return {
    left: Math.max(margin, Math.min(position.x + MASCOT_SIZE.width / 2 - width / 2, viewport.width - width - margin)),
    top: maxHeight <= roomAbove ? position.y - gap - maxHeight : maxHeight <= roomBelow ? position.y + MASCOT_SIZE.height + gap : margin,
    width,
    maxHeight,
  };
}
