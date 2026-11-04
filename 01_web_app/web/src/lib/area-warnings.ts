import type { Hazard, MapLayerId, OfficialWarning } from "@/lib/api";

/** Keep each hazard paired with the selected province's own periods, including future notices. */
export function areaWarnings(warnings: OfficialWarning[], code: string, hazard: MapLayerId,
  today = new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Bangkok" })) {
  const province = code.slice(0, 4);
  return warnings.flatMap(warning => {
    const hazards = warning.provinces_by_hazard
      ? Object.keys(warning.provinces_by_hazard) as Hazard[] : warning.hazards;
    const impacts = hazards.flatMap(h => {
      if (hazard !== "overview" && hazard !== h) return [];
      const provinces = warning.provinces_by_hazard ? warning.provinces_by_hazard[h] : warning.provinces;
      if (!provinces?.includes(province)) return [];
      const fallback = warning.impact_by_hazard?.[h]?.[province]
        ?? [warning.impact_from ?? null, warning.impact_to ?? null];
      const periods = warning.periods_by_hazard?.[h]?.[province];
      const days = periods ? periods.map(([from, to]) => from || to ? [from, to] : fallback) : [fallback];
      return days.filter(([, to]) => !to || to >= today)
        .filter(([from, to], i, all) => all.findIndex(d => d[0] === from && d[1] === to) === i)
        .map(([from, to]) => ({ hazard: h, from, to }));
    });
    return impacts.length ? [{ warning, impacts }] : [];
  });
}
