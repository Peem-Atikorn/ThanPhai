# ThanPhai UI review — 2026-10-19

## Scope
Frontend review and implementation in `01_web_app/web` (Next.js 16, React 19, MapLibre). Read the project architecture and progress notes to preserve existing hazard semantics and API contracts. Backend models and official-warning rules were not changed.

## Improvements
- Light, map-first layout with consistent navy controls, Thai typography, SVG icons, spacing, and responsive panels.
- Hazard controls moved outside the map; risk legend and data time have their own readable section.
- Province/district/subdistrict search uses the existing API, with result selection, map navigation, keyboard submission, empty and error states.
- Mobile shortcut jumps directly to area search. Touch controls, visible focus, a skip link, reduced-motion support, and telephone links are included.
- Selected area displays all hazard assessments, official-warning context, daily forecast, sources, degraded services, and Bangkok data times.
- New question suggestions prefill the existing assistant input. Submitting remains an explicit user action.
- Loading and failed layers cannot retain another hazard's polygons or show fabricated zero counts.
- Area requests clear old details and use request sequence IDs so a late response cannot overwrite a newer selection.
- Map resizes with its container and respects reduced motion.

## Verification
- TypeScript: `npx tsc --noEmit` passed.
- ESLint: `npm run lint` passed.
- Production: `docker compose build web` passed.
- Browser: all four hazard layers, area search, Enter submission, selecting Sena district, daily forecasts, no-result search, and assistant question/response worked against the real local API.
- Desktop, 768 px tablet, and 390 px mobile reviewed. No horizontal document overflow at tested sizes. Fixed mobile panel widths after screenshot inspection.
- No browser console errors observed during the tested workflow.
- Updated only the web container with `docker compose up -d --no-deps web`.

## Limits
API-failure/retry states were implemented and source-reviewed, but upstream outages were not deliberately induced. Backend model accuracy and all eight Python modules were outside this UI validation.

Screenshots: [desktop](desktop.png), [mobile](mobile.png). These capture live data at review time and are not current hazard reports.
