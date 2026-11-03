"use client";

import { useEffect, useSyncExternalStore } from "react";
import english from "@/lib/messages.en.json";
import observations from "@/lib/observation-patterns.json";

export type Language = "th" | "en";
const messages: Record<string, string> = english;
const observationPatterns = observations.map(item => ({ ...item, regex: new RegExp(item.pattern) }));
const listeners = new Set<() => void>();
let language: Language = "th";
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
const snapshot = () => language;
const serverSnapshot = (): Language => "th";

export const getLocale = () => language === "en" ? "en-GB" : "th-TH";

export function setLanguage(value: Language) {
  language = value;
  document.documentElement.lang = value;
  try { localStorage.setItem("thanphai-language", value); } catch { /* The switch also works when storage is unavailable. */ }
  listeners.forEach(listener => listener());
}

export function useLanguage() {
  const value = useSyncExternalStore(subscribe, snapshot, serverSnapshot);
  useEffect(() => {
    try { if (localStorage.getItem("thanphai-language") === "en") setLanguage("en"); } catch { /* Use Thai by default. */ }
  }, []);
  return value;
}

// Translate UI copy only. Unrecognised source content and place names stay intact.
export function t<T>(value: T): T {
  if (language !== "en" || typeof value !== "string") return value;
  const key = value.trim().replace(/\s+/g, " ");
  const translated = messages[key];
  if (translated !== undefined) return value.replace(value.trim(), translated) as T;
  for (const item of observationPatterns) {
    const match = item.regex.exec(value);
    if (!match) continue;
    const parts: string[] = [];
    item.parts.forEach((part, index) => {
      parts.push(t(part).trim());
      if (index + 1 < item.parts.length) parts.push(t(match[index + 1]).trim());
    });
    return parts.filter(Boolean).join(" ").replace(/\s+([,.%:)])/g, "$1").replace(/\(\s+/g, "(") as T;
  }
  return value;
}
