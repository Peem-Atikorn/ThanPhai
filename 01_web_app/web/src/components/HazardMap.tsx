"use client";

import type { ExpressionSpecification, StyleSpecification } from "@maplibre/maplibre-gl-style-spec";
import { type GeoJSONSource, Map as MapLibreMap, type MapLayerMouseEvent, NavigationControl, setWorkerUrl } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef } from "react";
import { type AreaBoundary, LEVELS } from "@/lib/api";

// Copied to public/ by scripts/copy-maplibre-worker.mjs (npm postinstall).
setWorkerUrl("/maplibre-gl-worker.mjs");

interface Props {
  data: GeoJSON.FeatureCollection | null;
  selected: string | null;
  onSelect: (code: string) => void;
  focus: { lat: number; lon: number; zoom?: number } | null;
  /** "points" hides the district risk layer and shows point features (dams, river stations) instead.
   * Each point needs properties `id`, `color` and `radius` (px). */
  mode?: "hazard" | "points";
  points?: GeoJSON.FeatureCollection | null;
  selectedPoint?: string | null;
  onSelectPoint?: (id: string) => void;
  label?: string;
  /** Searched province or sub-district: outlined, and the map fits to it. */
  outline?: AreaBoundary | null;
}

const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
const AREA_LAYERS = ["fill", "line", "selected"];
const POINT_LAYERS = ["points", "point-selected"];

// OSM raster tiles need no key. Follow the OSM tile usage policy; switch to GISTDA Sphere or a
// self-hosted tile server for heavier use (docs/API_REQUIREMENTS.md section 2).
const STYLE: StyleSpecification = {
  version: 8,
  sources: {
    osm: {
      type: "raster",
      tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
      tileSize: 256,
      attribution: "© OpenStreetMap contributors",
    },
  },
  layers: [{ id: "osm", type: "raster", source: "osm", paint: { "raster-saturation": -0.7, "raster-contrast": -0.15 } }],
};

const color = (id: string) => LEVELS.find((l) => l.id === id)!.color;
const fillColor: ExpressionSpecification = [
  "match",
  ["coalesce", ["get", "level"], "NONE"],
  "CRITICAL", color("CRITICAL"),
  "WARNING", color("WARNING"),
  "WATCH", color("WATCH"),
  "NORMAL", color("NORMAL"),
  color("NONE"),
];

function applyMode(m: MapLibreMap, mode: "hazard" | "points") {
  for (const id of AREA_LAYERS) m.setLayoutProperty(id, "visibility", mode === "hazard" ? "visible" : "none");
  for (const id of POINT_LAYERS) m.setLayoutProperty(id, "visibility", mode === "points" ? "visible" : "none");
}

export default function HazardMap({ data, selected, onSelect, focus, mode = "hazard", points = null, selectedPoint = null, onSelectPoint, label, outline = null }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const ready = useRef(false);
  // Latest props, so the map "load" handler can apply whatever arrived before the style was ready.
  const latest = useRef({ data, selected, onSelect, mode, points, selectedPoint, onSelectPoint, outline });

  useEffect(() => {
    latest.current = { data, selected, onSelect, mode, points, selectedPoint, onSelectPoint, outline };
  }, [data, selected, onSelect, mode, points, selectedPoint, onSelectPoint, outline]);

  useEffect(() => {
    if (!container.current) return;
    const m = new MapLibreMap({
      container: container.current,
      style: STYLE,
      center: [100.9, 13.4],
      zoom: 5,
      minZoom: 4.5,
      maxBounds: [[94, 4], [108, 22]],
    });
    m.addControl(new NavigationControl({ showCompass: false }), "top-right");
    m.on("load", () => {
      m.addSource("areas", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      m.addLayer({ id: "fill", type: "fill", source: "areas", paint: { "fill-color": fillColor, "fill-opacity": 0.55 } });
      m.addLayer({ id: "line", type: "line", source: "areas", paint: { "line-color": "#ffffff", "line-width": 0.4 } });
      m.addLayer({
        id: "selected",
        type: "line",
        source: "areas",
        paint: { "line-color": "#111827", "line-width": 2.5 },
        filter: ["==", ["get", "code"], ""],
      });
      // Searched area: a white casing under a dark line stays visible over every risk colour.
      m.addSource("outline", { type: "geojson", data: EMPTY });
      m.addLayer({ id: "outline-casing", type: "line", source: "outline", paint: { "line-color": "#ffffff", "line-width": 6, "line-opacity": 0.9 } });
      m.addLayer({ id: "outline-line", type: "line", source: "outline", paint: { "line-color": "#0f172a", "line-width": 2.8 } });
      m.on("click", "fill", (e: MapLayerMouseEvent) => {
        const code = e.features?.[0]?.properties?.code;
        if (code) latest.current.onSelect(code);
      });
      m.on("mouseenter", "fill", () => (m.getCanvas().style.cursor = "pointer"));
      m.on("mouseleave", "fill", () => (m.getCanvas().style.cursor = ""));
      // Points (dams, river stations): the caller sets each point's colour and radius.
      m.addSource("points", { type: "geojson", data: EMPTY });
      m.addLayer({
        id: "points", type: "circle", source: "points",
        // Big points first, so small ones stay clickable on top.
        layout: { "circle-sort-key": ["-", 0, ["coalesce", ["get", "radius"], 6]] },
        paint: {
          "circle-color": ["get", "color"],
          "circle-radius": ["coalesce", ["get", "radius"], 6],
          "circle-stroke-color": "#ffffff", "circle-stroke-width": 1.5, "circle-opacity": 0.9,
        },
      });
      m.addLayer({
        id: "point-selected", type: "circle", source: "points", filter: ["==", ["get", "id"], ""],
        paint: { "circle-color": "rgba(0,0,0,0)", "circle-radius": ["+", ["coalesce", ["get", "radius"], 6], 6], "circle-stroke-color": "#111827", "circle-stroke-width": 3 },
      });
      m.on("click", "points", (e: MapLayerMouseEvent) => {
        const id = e.features?.[0]?.properties?.id;
        if (id) latest.current.onSelectPoint?.(String(id));
      });
      m.on("mouseenter", "points", () => (m.getCanvas().style.cursor = "pointer"));
      m.on("mouseleave", "points", () => (m.getCanvas().style.cursor = ""));
      ready.current = true;
      const { data: d, selected: s, points: pts, selectedPoint: sp, mode: md, outline: ol } = latest.current;
      (m.getSource("outline") as GeoJSONSource).setData(ol ?? EMPTY);
      if (d) (m.getSource("areas") as GeoJSONSource).setData(d);
      m.setFilter("selected", ["==", ["get", "code"], s ?? ""]);
      (m.getSource("points") as GeoJSONSource).setData(pts ?? EMPTY);
      m.setFilter("point-selected", ["==", ["get", "id"], sp ?? ""]);
      applyMode(m, md);
    });
    map.current = m;
    const resize = new ResizeObserver(() => m.resize());
    resize.observe(container.current);
    return () => { resize.disconnect(); ready.current = false; map.current = null; m.remove(); };
  }, []);

  useEffect(() => {
    if (ready.current && map.current) (map.current.getSource("areas") as GeoJSONSource).setData(data ?? { type: "FeatureCollection", features: [] });
  }, [data]);

  useEffect(() => {
    if (ready.current && map.current) map.current.setFilter("selected", ["==", ["get", "code"], selected ?? ""]);
  }, [selected]);

  useEffect(() => {
    if (ready.current && map.current) (map.current.getSource("points") as GeoJSONSource).setData(points ?? EMPTY);
  }, [points]);

  useEffect(() => {
    if (ready.current && map.current) map.current.setFilter("point-selected", ["==", ["get", "id"], selectedPoint ?? ""]);
  }, [selectedPoint]);

  useEffect(() => {
    if (ready.current && map.current) applyMode(map.current, mode);
  }, [mode]);

  useEffect(() => {
    const m = map.current;
    if (!m) return;
    if (ready.current) (m.getSource("outline") as GeoJSONSource).setData(outline ?? EMPTY);
    if (outline?.bbox) {
      const [w, s, e, n] = outline.bbox;
      m.fitBounds([[w, s], [e, n]], { padding: 48, maxZoom: 11, duration: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 900 });
    }
  }, [outline]);

  useEffect(() => {
    if (focus && map.current) map.current.flyTo({ center: [focus.lon, focus.lat], zoom: focus.zoom ?? 9, duration: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 900 });
  }, [focus]);

  return <div ref={container} className="h-full w-full" role="region" aria-label={label ?? "แผนที่ความเสี่ยงรายอำเภอ"} />;
}
