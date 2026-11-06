"use client";

import { t as tr } from "@/lib/i18n";
import type { ExpressionSpecification, StyleSpecification } from "@maplibre/maplibre-gl-style-spec";
import { type GeoJSONSource, type ImageSource, Map as MapLibreMap, type MapLayerMouseEvent, NavigationControl, Popup, setWorkerUrl } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { useEffect, useRef } from "react";
import { type AreaBoundary, LEVELS, PM25_BANDS, SHELTER_COLOR } from "@/lib/api";
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
    /** DDPM shelters, each with `title`, `detail` (lines split by "\n") and `approximate` properties; nearby ones
     * merge into a circle with their count. */
    shelters?: GeoJSON.FeatureCollection | null;
    /** A route to a shelter: drawn over everything and fitted into view; red when it crosses flood water. */
    route?: { line: GeoJSON.LineString; crosses: boolean; water?: GeoJSON.Polygon | GeoJSON.MultiPolygon | null } | null;
    /** Dark basemap (inverted OSM), where the risk colours stand out more. */
    dark?: boolean;
    /** Hide districts that are NORMAL or have no data, so only the ones at WATCH or above are coloured. */
    riskOnly?: boolean;
    /** Radar tile URL template (RainViewer), drawn over the districts; null hides it. */
    radar?: string | null;
    /** The next frame is loaded invisibly so changing the slider does not flash or fetch at play time. */
    radarNext?: string | null;
    /** Called when every visible tile of a radar template (shown or preloaded) has loaded, or one of them failed. */
    onRadarImage?: (tiles: string, ok: boolean) => void;
    radarDimmed?: boolean;
    /** Forecast frame: the backend's motion (pixels of zoom-4 tile 12/7) to move the whole `radar` image by. */
    radarShift?: { dx: number; dy: number } | null;
    /** Amber projected tracks/positions for the selected forecast frame. */
    radarForecast?: GeoJSON.FeatureCollection | null;
    overlays?: {
        flood?: GeoJSON.FeatureCollection | null;
        cyclones?: GeoJSON.FeatureCollection | null;
        waves?: GeoJSON.FeatureCollection | null;
    };
}
const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
function routeFeatures(route: Props["route"]): GeoJSON.FeatureCollection {
    const points = route?.line.coordinates;
    if (!route || !points?.length)
        return EMPTY;
    const end = (position: GeoJSON.Position, name: string): GeoJSON.Feature => ({ type: "Feature", geometry: { type: "Point", coordinates: position }, properties: { end: name } });
    return { type: "FeatureCollection", features: [
        ...(route.water ? [{ type: "Feature" as const, geometry: route.water, properties: {} }] : []),
        { type: "Feature", geometry: route.line, properties: { crosses: route.crosses } },
        end(points[0], "start"), end(points[points.length - 1], "shelter"),
    ] };
}
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
// Most districts sit at WATCH on an ordinary day; a solid WATCH fill turned the whole country one colour and hid the
// few districts at WARNING/CRITICAL. WATCH and below stay as a faint tint, warnings stay strong.
const fillOpacity: ExpressionSpecification = [
    "match",
    ["coalesce", ["get", "level"], "NONE"],
    "CRITICAL", 0.8,
    "WARNING", 0.7,
    "WATCH", 0.2,
    "NORMAL", 0.12,
    0.05,
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
// The projected position of a rain cell: an amber rain cloud with a white edge, drawn at 2x for sharp screens.
function addRainCloudIcon(m: MapLibreMap) {
    const canvas = document.createElement("canvas");
    canvas.width = 64;
    canvas.height = 54;
    const ctx = canvas.getContext("2d")!;
    const cloud = (grow: number) => {
        ctx.beginPath();
        for (const [x, y, r] of [[19, 26, 10], [32, 18, 13], [45, 26, 10]])
            ctx.arc(x, y, r + grow, 0, Math.PI * 2);
        ctx.roundRect(9 - grow, 24 - grow, 46 + grow * 2, 14 + grow * 2, 7 + grow);
        ctx.fill();
    };
    ctx.fillStyle = "#ffffff";
    cloud(3);
    ctx.fillStyle = "#f59e0b";
    cloud(0);
    ctx.lineCap = "round";
    for (const x of [22, 32, 42]) {
        for (const [color, width] of [["#ffffff", 6], ["#f59e0b", 3]] as const) {
            ctx.strokeStyle = color;
            ctx.lineWidth = width;
            ctx.beginPath();
            ctx.moveTo(x, 43);
            ctx.lineTo(x - 3, 50);
            ctx.stroke();
        }
    }
    m.addImage("rain-cloud", ctx.getImageData(0, 0, canvas.width, canvas.height), { pixelRatio: 2 });
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
    // Opacity changes fade, so one frame dissolves into the next instead of jumping.
    m.addLayer({ id, type: "raster", source: id, paint: { "raster-opacity": opacity, "raster-opacity-transition": RADAR_FADE, "raster-resampling": "linear" } }, "line");
}
const RADAR_FADE = { duration: 500, delay: 0 };
// The nowcast measures one motion for the whole of zoom-4 tile 12/7 (lon 90–112.5°, lat 0–21.9°, all of Thailand).
// A forecast frame is the latest real image of that area, moved by that motion: the whole rain area moves, not just
// the cell centres. It is drawn from the four zoom-5 tiles of the same area (about 5 km a pixel), softened a little.
const SHIFT_TILE = { z: 4, x: 12, y: 7, size: 256 };
const SHIFT_IMAGE_ID = "radar-shift";
const EMPTY_IMAGE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=";
const shiftImages = new Map<string, Promise<string>>();
function shiftImageUrl(template: string): Promise<string> {
    let url = shiftImages.get(template);
    if (!url) {
        url = (async () => {
            const z = SHIFT_TILE.z + 1, size = 256;
            const tiles = await Promise.all([0, 1].flatMap(row => [0, 1].map(async col => {
                const src = template.replace("{z}", String(z)).replace("{x}", String(SHIFT_TILE.x * 2 + col)).replace("{y}", String(SHIFT_TILE.y * 2 + row));
                const res = await fetch(src);
                if (!res.ok) throw new Error(`radar tile ${res.status}`);
                return { col, row, image: await createImageBitmap(await res.blob()) };
            })));
            const sharp = document.createElement("canvas");
            sharp.width = sharp.height = size * 2;
            for (const t of tiles) sharp.getContext("2d")!.drawImage(t.image, t.col * size, t.row * size);
            // Blur the joined image once, so tile edges leave no seams.
            const soft = document.createElement("canvas");
            soft.width = soft.height = size * 2;
            const ctx = soft.getContext("2d")!;
            ctx.filter = "blur(1.5px)";
            ctx.drawImage(sharp, 0, 0);
            const blob = await new Promise<Blob | null>(done => soft.toBlob(done, "image/png"));
            if (!blob) throw new Error("radar image");
            return URL.createObjectURL(blob);
        })();
        url.catch(() => shiftImages.delete(template));
        shiftImages.set(template, url);
        // Keep the few most recent images; older frames have left the timeline.
        for (const key of [...shiftImages.keys()].slice(0, -3)) {
            shiftImages.get(key)?.then(u => URL.revokeObjectURL(u)).catch(() => {});
            shiftImages.delete(key);
        }
    }
    return url;
}
/** Corners of zoom-4 tile 12/7 moved by (dx, dy) of its pixels, as [lon, lat] from top-left clockwise. */
function shiftedCorners(dx: number, dy: number): [[number, number], [number, number], [number, number], [number, number]] {
    const world = SHIFT_TILE.size * 2 ** SHIFT_TILE.z;
    const lon = (px: number) => px / world * 360 - 180;
    const lat = (py: number) => Math.atan(Math.sinh(Math.PI * (1 - 2 * py / world))) * 180 / Math.PI;
    const x0 = SHIFT_TILE.x * SHIFT_TILE.size + dx, x1 = x0 + SHIFT_TILE.size;
    const y0 = SHIFT_TILE.y * SHIFT_TILE.size + dy, y1 = y0 + SHIFT_TILE.size;
    return [[lon(x0), lat(y0)], [lon(x1), lat(y0)], [lon(x1), lat(y1)], [lon(x0), lat(y1)]];
}
function addShiftLayer(m: MapLibreMap) {
    m.addSource(SHIFT_IMAGE_ID, { type: "image", url: EMPTY_IMAGE, coordinates: shiftedCorners(0, 0) });
    m.addLayer({ id: SHIFT_IMAGE_ID, type: "raster", source: SHIFT_IMAGE_ID, paint: { "raster-opacity": 0, "raster-opacity-transition": RADAR_FADE, "raster-resampling": "linear" } }, "line");
}
function applyRadar(m: MapLibreMap, radar: string | null, radarNext: string | null, sources: RadarSources, dimmed = false, shifted = false) {
    // Over district colours, below the lines and markers. A forecast hides the real image once its moved copy is shown;
    // until then (or if that copy cannot be made) the real image stays faint underneath.
    const opacity = dimmed ? (shifted ? 0 : 0.2) : 0.8;
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
export default function HazardMap({ data, selected, onSelect, focus, mode = "hazard", points = null, selectedPoint = null, onSelectPoint, label, outline = null, stations = null, shelters = null, route = null, radar = null, radarNext = null, onRadarImage, radarDimmed = false, radarShift = null, radarForecast = null, dark = false, riskOnly = false, overlays }: Props) {
    const container = useRef<HTMLDivElement>(null);
    const map = useRef<MapLibreMap | null>(null);
    const ready = useRef(false);
    // The radar template whose moved copy the forecast image layer currently holds.
    const shownShift = useRef<string | null>(null);
    const radarSources = useRef<RadarSources>({ activeId: "radar", activeTiles: null, preloadId: "radar-preload", preloadTiles: null });
    // Latest props, so the map "load" handler can apply whatever arrived before the style was ready.
    const latest = useRef({ data, selected, onSelect, mode, points, selectedPoint, onSelectPoint, outline, stations, shelters, route, radar, radarNext, radarDimmed, radarShift, radarForecast, dark, riskOnly, overlays });
    useEffect(() => {
        latest.current = { data, selected, onSelect, mode, points, selectedPoint, onSelectPoint, outline, stations, shelters, route, radar, radarNext, radarDimmed, radarShift, radarForecast, dark, riskOnly, overlays };
    }, [data, selected, onSelect, mode, points, selectedPoint, onSelectPoint, outline, stations, shelters, route, radar, radarNext, radarDimmed, radarShift, radarForecast, dark, riskOnly, overlays]);
    const radarImage = useRef(onRadarImage);
    useEffect(() => { radarImage.current = onRadarImage; }, [onRadarImage]);
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
        // Which radar template a source id holds right now (the two radar sources swap roles).
        const radarTiles = (id?: string) => {
            const s = radarSources.current;
            return id === s.activeId ? s.activeTiles : id === s.preloadId ? s.preloadTiles : null;
        };
        m.on("sourcedata", (e) => {
            const tiles = radarTiles(e.sourceId);
            // Only after a tile event, so a source that has just been given new tiles is not reported loaded early.
            if (tiles && e.tile && m.isSourceLoaded(e.sourceId)) radarImage.current?.(tiles, true);
        });
        m.on("error", (e: { sourceId?: string; error?: unknown }) => {
            const tiles = radarTiles(e.sourceId);
            if (tiles) radarImage.current?.(tiles, false);
            // A listener replaces MapLibre's own logging, so keep it for everything else.
            else console.error(e.error ?? e);
        });
        m.on("load", () => {
            m.addSource("areas", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
            m.addLayer({ id: "fill", type: "fill", source: "areas", paint: { "fill-color": fillColor, "fill-opacity": fillOpacity } });
            m.addLayer({ id: "line", type: "line", source: "areas", paint: { "line-color": "#ffffff", "line-width": 0.4, "line-opacity": ["interpolate", ["linear"], ["zoom"], 5, 0.25, 8, 0.6] } });
            m.addLayer({
                id: "selected",
                type: "line",
                source: "areas",
                paint: { "line-color": "#111827", "line-width": 2.5 },
                filter: ["==", ["get", "code"], ""],
            });
            addOverlays(m);
            addShiftLayer(m);
            m.addSource("radar-forecast", { type: "geojson", data: EMPTY });
            m.addLayer({ id: "radar-forecast-track", type: "line", source: "radar-forecast", filter: ["==", ["get", "kind"], "track"], paint: { "line-color": "#f59e0b", "line-width": 3, "line-dasharray": [2, 2] } }, "selected");
            addRainCloudIcon(m);
            // A stronger cell (higher dBZ) gets a slightly larger cloud.
            m.addLayer({ id: "radar-forecast-position", type: "symbol", source: "radar-forecast", filter: ["==", ["get", "kind"], "position"], layout: { "icon-image": "rain-cloud", "icon-size": ["interpolate", ["linear"], ["coalesce", ["get", "intensityDbz"], 25], 15, 0.9, 45, 1.3], "icon-allow-overlap": true, "icon-ignore-placement": true } }, "selected");
            (m.getSource("radar-forecast") as GeoJSONSource).setData(latest.current.radarForecast ?? EMPTY);
            applyOverlays(m, latest.current.overlays);
            // Searched area: a white casing under a dark line stays visible over every risk colour.
            m.addSource("outline", { type: "geojson", data: EMPTY });
            m.addLayer({ id: "outline-casing", type: "line", source: "outline", paint: { "line-color": "#ffffff", "line-width": 6, "line-opacity": 0.9 } });
            m.addLayer({ id: "outline-line", type: "line", source: "outline", paint: { "line-color": "#0f172a", "line-width": 2.8 } });
            m.on("click", "fill", (e: MapLayerMouseEvent) => {
                if (latest.current.mode === "risk" && m.queryRenderedFeatures(e.point, { layers: ["points"] }).length)
                    return;
                if (m.queryRenderedFeatures(e.point, { layers: ["shelter-dots"] }).length)
                    return;  // the shelter's own click handles it
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
            // DDPM shelters: ~10,000 points, so nearby ones merge into a counted circle until zoom 12.
            m.addSource("shelters", { type: "geojson", data: EMPTY, cluster: true, clusterRadius: 40, clusterMaxZoom: 12 });
            m.addLayer({
                id: "shelter-dots", type: "circle", source: "shelters",
                paint: {
                    "circle-color": ["case", ["has", "point_count"], SHELTER_COLOR, ["get", "approximate"], "#ffffff", SHELTER_COLOR],
                    "circle-radius": ["case", ["has", "point_count"], ["interpolate", ["linear"], ["get", "point_count"], 2, 12, 100, 17, 1000, 23], 6],
                    "circle-stroke-color": ["case", ["has", "point_count"], "#ffffff", ["get", "approximate"], SHELTER_COLOR, "#ffffff"],
                    "circle-stroke-width": ["case", ["has", "point_count"], 2.5, 2],
                    "circle-opacity": 0.92,
                },
            });
            m.addLayer({
                id: "shelter-counts", type: "symbol", source: "shelters", filter: ["has", "point_count"],
                layout: { "text-field": ["get", "point_count_abbreviated"], "text-font": ["Noto Sans Medium"], "text-size": 12, "text-allow-overlap": true },
                paint: { "text-color": "#ffffff" },
            });
            const shelterPopup = new Popup({ closeButton: true, offset: 10, maxWidth: "280px" });
            m.on("click", "shelter-dots", async (e: MapLayerMouseEvent) => {
                const f = e.features?.[0];
                if (f?.geometry.type !== "Point")
                    return;
                const at = f.geometry.coordinates as [number, number];
                const props = f.properties ?? {};
                if (props.cluster_id != null) {
                    const zoom = await (m.getSource("shelters") as GeoJSONSource).getClusterExpansionZoom(props.cluster_id);
                    m.easeTo({ center: at, zoom, duration: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 600 });
                    return;
                }
                // Text nodes only: the names come from an outside file.
                const body = document.createElement("div");
                body.className = "shelter-popup";
                const title = document.createElement("strong");
                title.textContent = String(props.title ?? "");
                body.append(title);
                for (const line of String(props.detail ?? "").split("\n").filter(Boolean)) {
                    const p = document.createElement("p");
                    p.textContent = line;
                    body.append(p);
                }
                const note = document.createElement("small");
                note.textContent = tr("ข้อมูลจาก ปภ. อาจไม่เป็นปัจจุบัน โปรดสอบถาม อปท. ในพื้นที่ หรือโทร 1784 ก่อนเดินทาง");
                body.append(note);
                shelterPopup.setLngLat(at).setDOMContent(body).addTo(m);
            });
            m.on("mouseenter", "shelter-dots", () => (m.getCanvas().style.cursor = "pointer"));
            m.on("mouseleave", "shelter-dots", () => (m.getCanvas().style.cursor = ""));
            // Route to a shelter, with both ends marked: on top of the shelters and stations.
            m.addSource("shelter-route", { type: "geojson", data: EMPTY });
            // The satellite water the route was checked against (finer than the district flood overlay).
            m.addLayer({ id: "shelter-route-water", type: "fill", source: "shelter-route", filter: ["in", ["geometry-type"], ["literal", ["Polygon", "MultiPolygon"]]],
                paint: { "fill-color": "#38bdf8", "fill-opacity": 0.55, "fill-outline-color": "#0284c7" } });
            m.addLayer({ id: "shelter-route-casing", type: "line", source: "shelter-route", filter: ["==", ["geometry-type"], "LineString"],
                layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": "#ffffff", "line-width": 8, "line-opacity": 0.9 } });
            m.addLayer({ id: "shelter-route-line", type: "line", source: "shelter-route", filter: ["==", ["geometry-type"], "LineString"],
                layout: { "line-cap": "round", "line-join": "round" }, paint: { "line-color": ["case", ["get", "crosses"], "#dc2626", "#2563eb"], "line-width": 4.5 } });
            m.addLayer({ id: "shelter-route-ends", type: "circle", source: "shelter-route", filter: ["==", ["geometry-type"], "Point"],
                paint: { "circle-radius": 7, "circle-color": ["case", ["==", ["get", "end"], "start"], "#2563eb", SHELTER_COLOR], "circle-stroke-color": "#ffffff", "circle-stroke-width": 2.5 } });
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
            const { data: d, selected: s, points: pts, selectedPoint: sp, mode: md, outline: ol, stations: st, shelters: sh, radar: rd, radarNext: rn, radarDimmed: dimmed, dark: dk, riskOnly: ro } = latest.current;
            (m.getSource("stations") as GeoJSONSource).setData(st ?? EMPTY);
            (m.getSource("shelters") as GeoJSONSource).setData(sh ?? EMPTY);
            (m.getSource("shelter-route") as GeoJSONSource).setData(routeFeatures(latest.current.route));
            applyRadar(m, rd, rn, radarSources.current, dimmed);
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
            (map.current.getSource("shelters") as GeoJSONSource).setData(shelters ?? EMPTY);
    }, [shelters]);
    useEffect(() => {
        const m = map.current;
        if (!ready.current || !m)
            return;
        (m.getSource("shelter-route") as GeoJSONSource).setData(routeFeatures(route));
        const points = route?.line.coordinates;
        if (!points?.length)
            return;
        const lons = points.map(p => p[0]), lats = points.map(p => p[1]);
        // After the next layout: the route summary appears with the route and resizes the map, and a resize
        // during a camera move left the map where it was. No animation, for the same reason.
        const frame = requestAnimationFrame(() => {
            m.resize();
            m.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], { padding: 50, maxZoom: 15, duration: 0 });
        });
        return () => cancelAnimationFrame(frame);
    }, [route]);
    useEffect(() => {
        const m = map.current;
        if (!ready.current || !m) return;
        const shift = radarDimmed && radar ? radarShift : null;
        const hide = () => {
            m.setPaintProperty(SHIFT_IMAGE_ID, "raster-opacity", 0);
            applyRadar(m, radar, radarNext, radarSources.current, radarDimmed, false);
        };
        if (!shift || !radar) { hide(); return; }
        let cancelled = false;
        // Until the moved copy is ready, the real image stays faint (the previous behaviour).
        applyRadar(m, radar, radarNext, radarSources.current, radarDimmed, shownShift.current === radar);
        shiftImageUrl(radar).then(url => {
            if (cancelled || map.current !== m) return;
            const source = m.getSource(SHIFT_IMAGE_ID) as ImageSource | undefined;
            if (!source) return;
            const coordinates = shiftedCorners(shift.dx, shift.dy);
            if (shownShift.current !== radar) source.updateImage({ url, coordinates });
            else source.setCoordinates(coordinates);
            shownShift.current = radar;
            m.setPaintProperty(SHIFT_IMAGE_ID, "raster-opacity", 0.75);
            applyRadar(m, radar, radarNext, radarSources.current, radarDimmed, true);
        }).catch(() => { if (!cancelled) hide(); });
        return () => { cancelled = true; };
    }, [radar, radarNext, radarDimmed, radarShift]);
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
