"use client";

import { t as tr } from "@/lib/i18n";
import type { ExpressionSpecification, StyleSpecification } from "@maplibre/maplibre-gl-style-spec";
import { type GeoJSONSource, Map as MapLibreMap, type MapLayerMouseEvent, NavigationControl, Popup, setWorkerUrl } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef } from "react";
import { type AreaBoundary, LEVELS, PM25_BANDS } from "@/lib/api";
// Copied to public/ by scripts/copy-maplibre-worker.mjs (npm postinstall).
setWorkerUrl("/maplibre-gl-worker.mjs");
interface Props {
    data: GeoJSON.FeatureCollection | null;
    selected: string | null;
    onSelect: (code: string) => void;
    focus: {
        lat: number;
        lon: number;
        zoom?: number;
    } | null;
    /** "points" hides the district risk layer and shows point features (dams, river stations) instead.
     * Each point needs properties `id`, `color` and `radius` (px). */
    mode?: "hazard" | "points" | "risk";
    points?: GeoJSON.FeatureCollection | null;
    selectedPoint?: string | null;
    onSelectPoint?: (id: string) => void;
    label?: string;
    /** Searched province or sub-district: outlined, and the map fits to it. */
    outline?: AreaBoundary | null;
    /** PM2.5 stations drawn over the districts, each with `pm25` and `title` properties. Nearby stations merge into
     * one circle that shows the highest value among them, so the worst air never disappears when zoomed out. */
    stations?: GeoJSON.FeatureCollection | null;
    /** Dark basemap (inverted OSM), where the risk colours stand out more. */
    dark?: boolean;
    /** Hide districts that are NORMAL or have no data, so only the ones at WATCH or above are coloured. */
    riskOnly?: boolean;
    /** Radar tile URL template (RainViewer), drawn over the districts; null hides it. */
    radar?: string | null;
    /** The next frame is loaded invisibly so changing the slider does not flash or fetch at play time. */
    radarNext?: string | null;
    /** Amber projected tracks/positions for the selected forecast frame. */
    radarForecast?: GeoJSON.FeatureCollection | null;
    overlays?: {
        flood?: GeoJSON.FeatureCollection | null;
        cyclones?: GeoJSON.FeatureCollection | null;
        waves?: GeoJSON.FeatureCollection | null;
    };
}
const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
const AREA_LAYERS = ["fill", "line", "selected"];
const POINT_LAYERS = ["points", "point-selected"];
// OSM raster tiles need no key. Follow the OSM tile usage policy; switch to GISTDA Sphere or a
// self-hosted tile server for heavier use (docs/API_REQUIREMENTS.md section 2).
const STYLE: StyleSpecification = {
    version: 8,
    // Glyphs for the station values (digits only). Protomaps hosts these fonts openly.
    glyphs: "https://protomaps.github.io/basemaps-assets/fonts/{fontstack}/{range}.pbf",
    sources: {
        osm: {
            type: "raster",
            tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
            tileSize: 256,
            attribution: "© OpenStreetMap contributors",
        },
    },
    layers: [
        { id: "osm", type: "raster", source: "osm", paint: { "raster-saturation": -0.7, "raster-contrast": -0.15 } },
        // Dark map: the same OSM tiles with brightness inverted (min above max), so no second tile service is needed.
        // CARTO's dark tiles were tried first; since 2026 they show "API KEY REQUIRED" without a key.
        { id: "dark", type: "raster", source: "osm", layout: { visibility: "none" },
            paint: { "raster-saturation": -1, "raster-brightness-min": 0.82, "raster-brightness-max": 0.1, "raster-contrast": 0.15 } },
    ],
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
// PM2.5 colour and label colour from the PCD bands, for single stations ("pm25") and merged ones ("max").
function bandStep(value: ExpressionSpecification, key: "color" | "text"): ExpressionSpecification {
    const stops = PM25_BANDS.slice(0, -1).flatMap((b, i) => [b.max + 0.05, PM25_BANDS[i + 1][key]]);
    return ["step", value, PM25_BANDS[0][key], ...stops] as ExpressionSpecification;
}
const stationValue: ExpressionSpecification = ["case", ["has", "point_count"], ["get", "max"], ["get", "pm25"]];
const RISK_LEVELS = ["WATCH", "WARNING", "CRITICAL"];
function addOverlays(m: MapLibreMap) {
    for (const id of ["flood", "cyclones", "waves"])
        m.addSource("overlay-" + id, { type: "geojson", data: EMPTY });
    m.addLayer({ id: "flood-fill", type: "fill", source: "overlay-flood", paint: { "fill-color": "#087ac1", "fill-opacity": ["interpolate", ["linear"], ["coalesce", ["get", "flood_share"], 0], 0, 0.15, 0.3, 0.65, 1, 0.85] } }, "selected");
    m.addLayer({ id: "flood-edge", type: "line", source: "overlay-flood", paint: { "line-color": "#087ac1", "line-width": 1.2 } }, "selected");
    m.addLayer({ id: "cyclone-wind", type: "fill", source: "overlay-cyclones", filter: ["==", ["get", "kind"], "wind_area"], layout: { "fill-sort-key": ["get", "wind_kmh"] }, paint: { "fill-color": fillColor, "fill-opacity": 0.2 } }, "selected");
    m.addLayer({ id: "cyclone-track", type: "line", source: "overlay-cyclones", filter: ["==", ["get", "kind"], "track"], paint: { "line-color": "#812773", "line-width": 3 } }, "selected");
    m.addLayer({ id: "cyclone-forecast", type: "line", source: "overlay-cyclones", filter: ["==", ["get", "kind"], "forecast"], paint: { "line-color": "#812773", "line-width": 3, "line-dasharray": [2, 2] } }, "selected");
    m.addLayer({ id: "cyclone-position", type: "circle", source: "overlay-cyclones", filter: ["==", ["get", "kind"], "track_point"], paint: { "circle-color": "#812773", "circle-radius": ["case", ["==", ["get", "latest"], true], 8, 3], "circle-stroke-width": 1.5, "circle-stroke-color": "#ffffff" } }, "selected");
    const canvas = document.createElement("canvas");
    canvas.width = canvas.height = 32;
    const ctx = canvas.getContext("2d")!;
    ctx.fillStyle = "white";
    ctx.beginPath();
    ctx.moveTo(16, 2);
    ctx.lineTo(29, 16);
    ctx.lineTo(21, 16);
    ctx.lineTo(21, 30);
    ctx.lineTo(11, 30);
    ctx.lineTo(11, 16);
    ctx.lineTo(3, 16);
    ctx.closePath();
    ctx.fill();
    m.addImage("wave-arrow", ctx.getImageData(0, 0, 32, 32), { sdf: true });
    m.addLayer({ id: "wave-arrows", type: "symbol", source: "overlay-waves", layout: { "icon-image": "wave-arrow", "icon-rotate": ["get", "direction_to_deg"], "icon-rotation-alignment": "map", "icon-size": ["interpolate", ["linear"], ["get", "height_m"], 0, 0.35, 4, 0.9, 8, 1.2], "icon-allow-overlap": true }, paint: { "icon-color": fillColor, "icon-halo-color": "#ffffff", "icon-halo-width": 1 } }, "selected");
}
function applyOverlays(m: MapLibreMap, overlays: Props["overlays"]) {
    for (const id of ["flood", "cyclones", "waves"] as const)
        (m.getSource("overlay-" + id) as GeoJSONSource).setData(overlays?.[id] ?? EMPTY);
}
function applyLook(m: MapLibreMap, dark: boolean, riskOnly: boolean) {
    m.setLayoutProperty("osm", "visibility", dark ? "none" : "visible");
    m.setLayoutProperty("dark", "visibility", dark ? "visible" : "none");
    m.setPaintProperty("line", "line-color", dark ? "#0b1220" : "#ffffff");
    m.setFilter("fill", riskOnly ? ["in", ["coalesce", ["get", "level"], "NONE"], ["literal", RISK_LEVELS]] : null);
}
// RainViewer serves zoom 0-7; MapLibre stretches zoom 7 tiles beyond that.
const RADAR_MAX_ZOOM = 7;
const RADAR_ATTRIBUTION = '<a href="https://www.rainviewer.com/" target="_blank" rel="noreferrer">RainViewer</a>';
type RadarSources = { activeId: string; activeTiles: string | null; preloadId: string; preloadTiles: string | null };
function applyRadarSource(m: MapLibreMap, id: string, radar: string | null, opacity: number, attribution = "") {
    if (!radar) {
        if (m.getLayer(id)) m.removeLayer(id);
        if (m.getSource(id)) m.removeSource(id);
        return;
    }
    const source = m.getSource(id) as { setTiles?: (tiles: string[]) => void } | undefined;
    if (source?.setTiles) {
        source.setTiles([radar]);
        m.setPaintProperty(id, "raster-opacity", opacity);
        return;
    }
    m.addSource(id, { type: "raster", tiles: [radar], tileSize: 256, maxzoom: RADAR_MAX_ZOOM, attribution });
    // The invisible second layer preloads just one frame, keeping tile traffic bounded while playback is running.
    m.addLayer({ id, type: "raster", source: id, paint: { "raster-opacity": opacity } }, "line");
}
function applyRadar(m: MapLibreMap, radar: string | null, radarNext: string | null, sources: RadarSources) {
    // Over district colours, below the lines and markers.
    const opacity = 0.8;
    if (radar && radar === sources.preloadTiles) {
        [sources.activeId, sources.preloadId] = [sources.preloadId, sources.activeId];
        [sources.activeTiles, sources.preloadTiles] = [sources.preloadTiles, sources.activeTiles];
        m.setPaintProperty(sources.preloadId, "raster-opacity", 0);
    }
    if (sources.activeTiles !== radar) {
        applyRadarSource(m, sources.activeId, radar, opacity, RADAR_ATTRIBUTION);
        sources.activeTiles = radar;
    }
    if (radar && m.getLayer(sources.activeId))
        m.setPaintProperty(sources.activeId, "raster-opacity", opacity);
    if (sources.preloadTiles !== radarNext) {
        applyRadarSource(m, sources.preloadId, radarNext, 0, RADAR_ATTRIBUTION);
        sources.preloadTiles = radarNext;
    }
}
function applyMode(m: MapLibreMap, mode: "hazard" | "points" | "risk") {
    for (const id of AREA_LAYERS)
        m.setLayoutProperty(id, "visibility", mode !== "points" ? "visible" : "none");
    for (const id of POINT_LAYERS)
        m.setLayoutProperty(id, "visibility", mode !== "hazard" ? "visible" : "none");
}
export default function HazardMap({ data, selected, onSelect, focus, mode = "hazard", points = null, selectedPoint = null, onSelectPoint, label, outline = null, stations = null, radar = null, radarNext = null, radarForecast = null, dark = false, riskOnly = false, overlays }: Props) {
    const container = useRef<HTMLDivElement>(null);
    const map = useRef<MapLibreMap | null>(null);
    const ready = useRef(false);
    const radarSources = useRef<RadarSources>({ activeId: "radar", activeTiles: null, preloadId: "radar-preload", preloadTiles: null });
    // Latest props, so the map "load" handler can apply whatever arrived before the style was ready.
    const latest = useRef({ data, selected, onSelect, mode, points, selectedPoint, onSelectPoint, outline, stations, radar, radarNext, radarForecast, dark, riskOnly, overlays });
    useEffect(() => {
        latest.current = { data, selected, onSelect, mode, points, selectedPoint, onSelectPoint, outline, stations, radar, radarNext, radarForecast, dark, riskOnly, overlays };
    }, [data, selected, onSelect, mode, points, selectedPoint, onSelectPoint, outline, stations, radar, radarNext, radarForecast, dark, riskOnly, overlays]);
    useEffect(() => {
        if (!container.current)
            return;
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
            addOverlays(m);
            m.addSource("radar-forecast", { type: "geojson", data: EMPTY });
            m.addLayer({ id: "radar-forecast-track", type: "line", source: "radar-forecast", filter: ["==", ["get", "kind"], "track"], paint: { "line-color": "#f59e0b", "line-width": 3, "line-dasharray": [2, 2] } }, "selected");
            m.addLayer({ id: "radar-forecast-position", type: "circle", source: "radar-forecast", filter: ["==", ["get", "kind"], "position"], paint: { "circle-color": "#f59e0b", "circle-radius": 6, "circle-stroke-color": "#fff", "circle-stroke-width": 2 } }, "selected");
            (m.getSource("radar-forecast") as GeoJSONSource).setData(latest.current.radarForecast ?? EMPTY);
            applyOverlays(m, latest.current.overlays);
            // Searched area: a white casing under a dark line stays visible over every risk colour.
            m.addSource("outline", { type: "geojson", data: EMPTY });
            m.addLayer({ id: "outline-casing", type: "line", source: "outline", paint: { "line-color": "#ffffff", "line-width": 6, "line-opacity": 0.9 } });
            m.addLayer({ id: "outline-line", type: "line", source: "outline", paint: { "line-color": "#0f172a", "line-width": 2.8 } });
            m.on("click", "fill", (e: MapLayerMouseEvent) => {
                if (latest.current.mode === "risk" && m.queryRenderedFeatures(e.point, { layers: ["points"] }).length)
                    return;
                const code = e.features?.[0]?.properties?.code;
                if (code)
                    latest.current.onSelect(code);
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
                if (id)
                    latest.current.onSelectPoint?.(String(id));
            });
            m.on("mouseenter", "points", () => (m.getCanvas().style.cursor = "pointer"));
            m.on("mouseleave", "points", () => (m.getCanvas().style.cursor = ""));
            // PM2.5 stations. Stations close together merge into one circle showing their highest value (bigger circle =
            // more stations); zooming in splits them. Bangkok alone has ~70 stations.
            m.addSource("stations", { type: "geojson", data: EMPTY, cluster: true, clusterRadius: 34, clusterMaxZoom: 10,
                clusterProperties: { max: ["max", ["get", "pm25"]] } });
            m.addLayer({
                id: "station-dots", type: "circle", source: "stations",
                paint: {
                    "circle-color": bandStep(stationValue, "color"),
                    // "zoom" may only drive a top-level interpolate, so the cluster size sits inside each zoom stop.
                    "circle-radius": ["interpolate", ["linear"], ["zoom"],
                        5, ["case", ["has", "point_count"], ["interpolate", ["linear"], ["get", "point_count"], 2, 13, 20, 18, 70, 23], 9],
                        10, ["case", ["has", "point_count"], ["interpolate", ["linear"], ["get", "point_count"], 2, 13, 20, 18, 70, 23], 13]],
                    "circle-stroke-color": "#ffffff", "circle-stroke-width": ["case", ["has", "point_count"], 3, 1.5],
                },
            });
            m.addLayer({
                id: "station-values", type: "symbol", source: "stations",
                layout: { "text-field": ["to-string", ["round", stationValue]], "text-font": ["Noto Sans Medium"],
                    "text-size": ["case", ["has", "point_count"], 13, 11], "text-allow-overlap": true },
                paint: { "text-color": bandStep(stationValue, "text") },
            });
            const popup = new Popup({ closeButton: false, offset: 12 });
            m.on("click", "station-dots", async (e: MapLayerMouseEvent) => {
                const f = e.features?.[0];
                if (f?.geometry.type !== "Point")
                    return;
                const at = f.geometry.coordinates as [
                    number,
                    number
                ];
                const props = f.properties ?? {};
                if (props.cluster_id == null) {
                    popup.setLngLat(at).setText(String(props.title ?? "")).addTo(m);
                    return;
                }
                popup.setLngLat(at).setText(tr("") + tr(props.point_count) + tr(" สถานี \u00B7 ค่าสูงสุด ") + tr(Number(props.max).toFixed(1)) + tr(" มคก./ลบ.ม.")).addTo(m);
                const zoom = await (m.getSource("stations") as GeoJSONSource).getClusterExpansionZoom(props.cluster_id);
                m.easeTo({ center: at, zoom, duration: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 600 });
            });
            m.on("mouseenter", "station-dots", () => (m.getCanvas().style.cursor = "pointer"));
            m.on("mouseleave", "station-dots", () => (m.getCanvas().style.cursor = ""));
            ready.current = true;
            const { data: d, selected: s, points: pts, selectedPoint: sp, mode: md, outline: ol, stations: st, radar: rd, radarNext: rn, dark: dk, riskOnly: ro } = latest.current;
            (m.getSource("stations") as GeoJSONSource).setData(st ?? EMPTY);
            applyRadar(m, rd, rn, radarSources.current);
            applyLook(m, dk, ro);
            (m.getSource("outline") as GeoJSONSource).setData(ol ?? EMPTY);
            if (d)
                (m.getSource("areas") as GeoJSONSource).setData(d);
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
        if (ready.current && map.current)
            (map.current.getSource("areas") as GeoJSONSource).setData(data ?? { type: "FeatureCollection", features: [] });
    }, [data]);
    useEffect(() => {
        if (ready.current && map.current)
            map.current.setFilter("selected", ["==", ["get", "code"], selected ?? ""]);
    }, [selected]);
    useEffect(() => {
        if (ready.current && map.current)
            (map.current.getSource("points") as GeoJSONSource).setData(points ?? EMPTY);
    }, [points]);
    useEffect(() => {
        if (ready.current && map.current)
            map.current.setFilter("point-selected", ["==", ["get", "id"], selectedPoint ?? ""]);
    }, [selectedPoint]);
    useEffect(() => {
        if (ready.current && map.current)
            applyMode(map.current, mode);
    }, [mode]);
    useEffect(() => {
        if (ready.current && map.current)
            (map.current.getSource("stations") as GeoJSONSource).setData(stations ?? EMPTY);
    }, [stations]);
    useEffect(() => {
        if (ready.current && map.current)
        applyRadar(map.current, radar, radarNext, radarSources.current);
    }, [radar, radarNext]);
    useEffect(() => {
        if (ready.current && map.current)
            (map.current.getSource("radar-forecast") as GeoJSONSource).setData(radarForecast ?? EMPTY);
    }, [radarForecast]);
    useEffect(() => {
        if (ready.current && map.current)
            applyOverlays(map.current, overlays);
    }, [overlays]);
    useEffect(() => {
        if (ready.current && map.current)
            applyLook(map.current, dark, riskOnly);
    }, [dark, riskOnly]);
    useEffect(() => {
        const m = map.current;
        if (!m)
            return;
        if (ready.current)
            (m.getSource("outline") as GeoJSONSource).setData(outline ?? EMPTY);
        if (outline?.bbox) {
            const [w, s, e, n] = outline.bbox;
            const padding = Math.min(48, (container.current?.clientWidth ?? 0) * .15, (container.current?.clientHeight ?? 0) * .15);
            m.fitBounds([[w, s], [e, n]], { padding, maxZoom: 11, duration: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 900 });
        }
    }, [outline]);
    useEffect(() => {
        if (focus && map.current)
            map.current.flyTo({ center: [focus.lon, focus.lat], zoom: focus.zoom ?? 9, duration: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 900 });
    }, [focus]);
    return <div ref={container} className="h-full w-full" role="region" aria-label={tr(label ?? "แผนที่ความเสี่ยงรายอำเภอ")}/>;
}
