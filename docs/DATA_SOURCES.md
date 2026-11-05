# Data Sources — ThanPhai

Candidate sources for each hazard. **Every source must be checked before use:** API access, registration or key, rate limits, license, and required attribution. Mark the status column as the team verifies each one.

| Source | Provider | Data | Used for | Access | Status |
|---|---|---|---|---|---|
| TMD Open Data API | Thai Meteorological Department (กรมอุตุนิยมวิทยา) | Weather warnings, 3-hour observations, 7-day forecast | Rain/storm, flood, fire, official warnings | uid/ukey (public demo pair works) | ✅ live-tested 2026-10-19 |
| TMD NWP API | Thai Meteorological Department | WRF hourly/daily forecast at lat-lon | Rain/storm, flood | Bearer token (register) | 🔑 endpoint confirmed, need token |
| ThaiWater | HII (สถาบันสารสนเทศทรัพยากรน้ำ) | Rainfall stations, water level, dam storage | Flood, drought | Public API, no key | ✅ live-tested 2026-10-19 |
| RID | Royal Irrigation Department (กรมชลประทาน) | Reservoir and river data | Flood, drought | Dam data already in ThaiWater `thailand_main` | ✅ via ThaiWater |
| GISTDA API Gateway | GISTDA (สทอภ.) | Satellite flood extent (1/3/7/30 days), hotspots | Flood (labels, map), fire | `API-Key` header (register) | 🔑 endpoint confirmed, need key |
| GISTDA Open Data | GISTDA | Flood datasets, WMS/WMTS layers | Flood history, map layers | CKAN API, no key | ✅ live-tested 2026-10-19 |
| NASA FIRMS | NASA | VIIRS / MODIS active fire hotspots | Fire | Keyless 24h/48h/7d CSV; MAP_KEY for area API | ✅ keyless file tested · 🔑 for history |
| DDPM | Department of Disaster Prevention and Mitigation (ปภ.) | Declared disaster areas, guides | Labels, RAG documents | No public API found; manual download + data.go.th | 📄 manual |
| data.go.th | Digital Government Development Agency | Admin boundaries, historical disaster datasets | Reference data, labels | CKAN API, no key | ✅ live-tested 2026-10-19 |
| Open-Meteo | Open-Meteo | Forecast, ERA5 history, GloFAS river discharge | Fallback forecast, training history, flood | No key; non-commercial free, 10,000 calls/day | ✅ live-tested 2026-10-19 |
| Air4Thai | Pollution Control Department (กรมควบคุมมลพิษ) | PM2.5 and AQI at ~170 stations, hourly | Air quality | No key | ✅ live-tested 2026-10-22 |
| Open-Meteo Air Quality | Open-Meteo / CAMS | PM2.5 forecast for any lat-lon | Air quality where there is no station | No key; each coordinate counts as a call | ✅ live-tested 2026-10-22 |
| TMD earthquakes | กรมอุตุนิยมวิทยา (กองเฝ้าระวังแผ่นดินไหว) | Latest earthquakes, Thai place text | Earthquake | RSS, no key | ✅ live-tested 2026-10-22 |
| USGS | U.S. Geological Survey | Earthquakes M >= 3.5 around Thailand | Earthquake | No key | ✅ live-tested 2026-10-22 |
| GDACS | UN / European Commission | Regional earthquakes, cyclones, floods with impact alerts | Context, cyclones | No key | ✅ live-tested 2026-10-22 |
| DMR landslide inventory | กรมทรัพยากรธรณี | 56,177 past landslide points (1989–2019) | Landslide (where to check rain) | CKAN, no key, CC BY | ✅ downloaded 2026-10-22 |
| CHIRPS | UCSB Climate Hazards Center | Historical gridded rainfall | Drought (SPI) | File download, no key | ✅ reachable |
| MODIS / Sentinel-2 | NASA / ESA (Copernicus) | NDVI vegetation index | Drought, fire | Free Earthdata / Copernicus login | 📄 need login |
| SRTM / Copernicus DEM | NASA / ESA | Elevation | Flood features | Open download | 📄 not tested |
| HDX admin boundaries | OCHA | Thailand admin levels 0–3 with P-codes | Area mapping, gazetteer | CKAN API, no key | ✅ live-tested 2026-10-19 |

Full details (endpoints, limits, licenses, keys): [API_REQUIREMENTS.md](API_REQUIREMENTS.md)

## Minimum data per hazard (MVP)

| Hazard | Must have | Nice to have |
|---|---|---|
| Rain / storm | TMD forecast + warnings | Open-Meteo fallback, station rainfall |
| Flood | Station rainfall + water level, DEM, admin boundaries | Reservoir levels, GISTDA flood extent history |
| Drought | Historical rainfall (CHIRPS) for SPI | NDVI, soil moisture, DDPM drought declarations |
| Fire | FIRMS hotspots + weather | GISTDA hotspots, NDVI, land cover |
| Air quality (PM2.5) | Air4Thai stations | Open-Meteo CAMS forecast |

## Rules

- Use official APIs and open data. Do not scrape sites that forbid it.
- Keep API keys in `.env` only and never commit them.
- Show the provider's attribution text wherever its data is displayed.
- Official warnings always take priority over model output.
