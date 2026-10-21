import type { SVGProps } from "react";

export type IconName = "shield" | "map" | "search" | "pin" | "flood" | "rain_storm" | "fire" | "drought" | "air_quality" | "heat" | "earthquake" | "marine" | "tropical_cyclone" | "arrow" | "refresh" | "clock" | "info" | "close" | "chat" | "phone" | "dam" | "river" | "radar";
const paths: Record<IconName, React.ReactNode> = {
  shield: <><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z" /><path d="m8 12 3 3 5-6" /></>,
  map: <><path d="m9 18-6 3V6l6-3 6 3 6-3v15l-6 3-6-3Z" /><path d="M9 3v15M15 6v15" /></>,
  search: <><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 5 5" /></>,
  pin: <><path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 1 1 14 0Z" /><circle cx="12" cy="10" r="2.5" /></>,
  flood: <><path d="M3 16c3-4 6 4 9 0s6 4 9 0M3 21c3-4 6 4 9 0s6 4 9 0M6 12V8l6-5 6 5v4M10 12V9h4v3" /></>,
  rain_storm: <><path d="M6 14a4 4 0 0 1 0-8 6 6 0 0 1 11-1 4.5 4.5 0 0 1 1 9M6 18l-1 3m6-4-1 3m7-2-1 3" /><path d="m13 9-3 5h4l-2 4" /></>,
  fire: <path d="M13 2s2 5-2 8c-2-1-2-3-2-3s-5 5-5 9a8 8 0 0 0 16 0c0-5-4-8-4-8s0 4-2 5c2-6-1-11-1-11Z" />,
  drought: <><circle cx="12" cy="10" r="4" /><path d="M12 1v2M3 10H1m22 0h-2M4 3l2 2m14-2-2 2M3 18h18M5 22l4-4 3 4 3-4 4 4" /></>,
  // Drifting haze lines with fine particles.
  air_quality: <><path d="M3 8h10a3 3 0 1 0-3-3M3 12h15a3 3 0 1 1-3 3M3 16h8" /><path d="M18 6v.1M21 9v.1M14 20v.1M19 20v.1" /></>,
  // Thermometer with heat rays.
  heat: <><path d="M10 14.5V4a2 2 0 1 1 4 0v10.5a4 4 0 1 1-4 0Z" /><path d="M12 9v7M18 6l2-1M18 10h2.5M18 14l2 1" /></>,
  // Seismograph trace over cracked ground.
  earthquake: <><path d="M2 11h4l2-5 3 10 3-8 2 3h6" /><path d="M3 19h7l2-2 2 2h7" /></>,
  // Boat on a breaking wave.
  marine: <><path d="M2 18c2.5-2 4.5 2 7 0s4.5 2 7 0 3.5 1 6 0" /><path d="M5 14h12l-2 3H7Z" /><path d="M11 14V5l5 6h-5" /></>,
  // Spiral of a cyclone around its eye.
  tropical_cyclone: <><circle cx="12" cy="12" r="2.5" /><path d="M12 3a9 9 0 0 1 9 9c0 3-2 5-4 5.5M12 21a9 9 0 0 1-9-9c0-3 2-5 4-5.5" /></>,
  arrow: <path d="M5 12h14m-5-5 5 5-5 5" />,
  refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6 6a8 8 0 0 1 13 3M5 15a8 8 0 0 0 13 3" /></>,
  clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  info: <><circle cx="12" cy="12" r="9" /><path d="M12 11v6m0-10v.1" /></>,
  close: <path d="m6 6 12 12M18 6 6 18" />,
  chat: <><path d="M21 11a8 8 0 0 1-8 8H7l-4 3V7a4 4 0 0 1 4-4h6a8 8 0 0 1 8 8Z" /><path d="M7 9h10M7 13h6" /></>,
  // Dam wall holding water on the left, released water on the right.
  dam: <><path d="M3 9h7v12H3" /><path d="M10 5h3l3 16h-6" /><path d="M16 15c1.5-1 3 1 5 0M16 19c1.5-1 3 1 5 0" /></>,
  river: <><path d="M3 7c3-2 6 2 9 0s6-2 9 0" /><path d="M3 12c3-2 6 2 9 0s6-2 9 0" /><path d="M3 17c3-2 6 2 9 0s6-2 9 0" /></>,
  radar: <><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><path d="M12 12l6-6" /><circle cx="12" cy="12" r="1" /></>,
  phone: <path d="m8 3 2 5-3 2a14 14 0 0 0 7 7l2-3 5 2v3a2 2 0 0 1-2 2A18 18 0 0 1 3 5a2 2 0 0 1 2-2h3Z" />,
};
export default function Icon({ name, ...props }: SVGProps<SVGSVGElement> & { name: IconName }) {
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{paths[name]}</svg>;
}
