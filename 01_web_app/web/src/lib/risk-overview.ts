import { HAZARDS, type Hazard } from "@/lib/api";
import type { RiskFilter } from "@/components/RiskSnapshot";

export type RiskArea = { code: string; name: string; province: string; hazard: Hazard | null; hazards: number; center: [number, number] };

export function riskAreasAtLevel(data: GeoJSON.FeatureCollection | null, level: RiskFilter): RiskArea[] {
  return (data?.features ?? []).filter(f => f.properties?.level === level).flatMap(f => {
    const coordinates = f.geometry?.type === "Polygon" ? f.geometry.coordinates.flat() : f.geometry?.type === "MultiPolygon" ? f.geometry.coordinates.flat(2) : [];
    if (!coordinates.length || !f.properties?.code) return [];
    let west = Infinity, east = -Infinity, south = Infinity, north = -Infinity;
    for (const [x, y] of coordinates) { west = Math.min(west, x); east = Math.max(east, x); south = Math.min(south, y); north = Math.max(north, y); }
    const p = f.properties;
    return [{ code: String(p.code), name: String(p.name_th ?? p.code), province: String(p.province_th ?? ""), hazard: HAZARDS.find(h => h.id === p.worst_hazard)?.id ?? null, hazards: Number(p.hazards_at_risk ?? 1), center: [(west + east) / 2, (south + north) / 2] as [number, number] }];
  }).sort((a, b) => a.province.localeCompare(b.province, "th") || a.name.localeCompare(b.name, "th"));
}
