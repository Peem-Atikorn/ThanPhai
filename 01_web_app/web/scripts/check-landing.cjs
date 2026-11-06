const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const ts = require('typescript');
const root = path.resolve(__dirname, '..');

// Execute the actual components and event handlers with deterministic hooks/API fixtures.
// This checks state transitions; it is not browser/layout verification.
let current;
const hooks = {
  useState(initial) { const i = current.cursor++; const host = current; if (!(i in host.state)) host.state[i] = typeof initial === 'function' ? initial() : initial; return [host.state[i], value => { host.state[i] = typeof value === 'function' ? value(host.state[i]) : value; }]; },
  useRef(initial) { const i = current.cursor++; return current.state[i] ??= { current: initial }; },
  useMemo(fn) { current.cursor++; return fn(); },
  useCallback(fn) { current.cursor++; return fn; },
  useEffect(fn, dependencies) {
    fn.dependencies = dependencies;
    const i = current.cursor++;
    if (!current.trackEffects) { current.effects?.push(fn); return; }
    const previous = current.effectDeps[i];
    if (previous && dependencies?.length === previous.length && dependencies.every((value, index) => value === previous[index])) return;
    current.effectCleanups[i]?.();
    current.effectDeps[i] = dependencies;
    current.effectCleanups[i] = fn();
  },
  useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot) { return (getServerSnapshot ?? getSnapshot)(); },
};
const jsx = (type, props) => ({ type, props: props || {} });
const cache = {};
const observed = { search: 0 };
function load(id) {
  if (id === 'react') return hooks;
  if (id === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: 'fragment' };
  if (id === 'react-dom') return { createPortal: node => node };
  if (id === 'next/dynamic') return { __esModule: true, default: () => () => null };
  if (id === 'next/link') return { __esModule: true, default: 'a' };
  if (id.endsWith('.json')) return JSON.parse(fs.readFileSync(path.join(root, 'src', id.slice(2)), 'utf8'));
  if (cache[id]) return cache[id].exports;
  let file = path.join(root, 'src', id.slice(2));
  file += fs.existsSync(file + '.tsx') ? '.tsx' : '.ts';
  const js = ts.transpileModule(fs.readFileSync(file, 'utf8'), { fileName: file, compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true } }).outputText;
  const mod = cache[id] = { exports: {} };
  new Function('module', 'exports', 'require', js)(mod, mod.exports, load);
  return mod.exports;
}
function instance(Component, effects, trackEffects = false) {
  const state = [];
  const effectDeps = [], effectCleanups = [];
  return { state, render(props = {}) { current = { state, cursor: 0, effects, trackEffects, effectDeps, effectCleanups }; this.tree = Component(props); return this.tree; } };
}
function nodes(node, result = []) {
  if (Array.isArray(node)) { node.forEach(n => nodes(n, result)); return result; }
  if (!node || typeof node !== 'object') return result;
  result.push(node); nodes(node.props?.children, result); return result;
}
function content(node) { if (Array.isArray(node)) return node.map(content).join(''); if (typeof node === 'string' || typeof node === 'number') return String(node); return node && typeof node === 'object' ? content(node.props?.children) : ''; }
const tick = () => new Promise(resolve => setImmediate(resolve));
global.requestAnimationFrame = fn => fn();
global.window = { matchMedia: () => ({ matches: true }), setInterval: () => 0, clearInterval() {} };
global.document = { documentElement: { lang: 'th' }, getElementById: () => ({ focus() {}, scrollIntoView() {} }) };

(async () => {
  const lib = load('@/lib/api');
  const { CurrentTime } = load('@/components/DataClock');
  assert.equal(typeof CurrentTime, 'function', 'The radar clock can show current time independently of image time');
  const savedNow = Date.now, savedStore = hooks.useSyncExternalStore;
  try {
    let now = Date.UTC(2026, 10, 5, 7, 10);
    Date.now = () => now;
    hooks.useSyncExternalStore = (subscribe, getSnapshot) => getSnapshot();
    const clock = instance(CurrentTime);
    let currentClock = clock.render();
    assert.equal(currentClock.props.dateTime, '2026-11-05T07:10:00.000Z');
    assert(content(currentClock).includes('14:10'), 'Current time uses the Bangkok timezone');
    now += 60000;
    currentClock = clock.render();
    assert(content(currentClock).includes('14:11'), 'Current time follows the clock instead of the selected radar frame');
  } finally { Date.now = savedNow; hooks.useSyncExternalStore = savedStore; }

  const savedFetch = global.fetch;
  let nowcastUrl = '';
  global.fetch = async url => {
    nowcastUrl = String(url);
    return { status: 200, ok: true, json: async () => ({
      status: 'ok',
      past_frames: [{ time: 1800000000, tile_url: 'https://tiles.test/observed/{z}/{x}/{y}.png' }],
      forecast_frames: [{ time: 1800000600, horizon_min: 10, dx_pixels: 2, dy_pixels: -1 }],
      motion: { speed_kmh: 36, direction_deg: 90, direction_th: 'ตะวันออก' },
      rain_cells: [{ id: 'cell-1', centroid_lat: 13.5, centroid_lon: 100.5, peak_intensity_dbz: 42 }],
      explanation: 'กลุ่มฝนเคลื่อนตัวไปทางตะวันออก',
      explanation_source: 'qwen',
    }) };
  };
  const nowcastResult = await lib.api.radarNowcast();
  assert.equal(nowcastUrl, '/api/v1/radar/nowcast', 'Nowcast is read through the same-origin backend proxy');
  assert.equal(nowcastResult.enabled, true, 'An enabled backend exposes the nowcast');
  assert.equal(nowcastResult.past_frames[0].tile_url, 'https://tiles.test/observed/{z}/{x}/{y}.png');
  assert.equal(nowcastResult.forecast_frames[0].horizon_min, 10);
  global.fetch = async () => ({ status: 404, ok: false });
  assert.equal((await lib.api.radarNowcast()).enabled, false, 'The default-off backend cleanly disables nowcast');
  global.fetch = savedFetch;
  const { highestEnvironmentValue, default: EnvironmentSummary } = load('@/components/EnvironmentSummary');
  const environmentLayer = { type: 'FeatureCollection', meta: { data_updated_at: '2026-11-04T05:00:00Z', degraded_services: [] }, features: [
    { properties: { name_th: 'ไม่มีค่า', pm25_24h: null, heat_index_now: null, heat_index_max_24h: null } },
    { properties: { name_th: 'อำเภอ ก', province_th: 'เชียงใหม่', pm25_24h: 80, heat_index_now: 33, heat_index_max_24h: 41, rain_chance_max_24h: 63, rain_chance_peak_hour: 18, level: 'NORMAL' } },
    { properties: { name_th: 'อำเภอ ข', province_th: 'สงขลา', pm25_24h: 0, heat_index_now: 35, heat_index_max_24h: 39, rain_chance_max_24h: 40, rain_chance_peak_hour: 9, level: 'WARNING' } },
  ] };
  assert.equal(highestEnvironmentValue(environmentLayer, 'air_quality').name, 'อำเภอ ก');
  // The heat card ranks districts by how hot it feels now; the 24-h peak of that district rides along.
  const hottestNow = highestEnvironmentValue(environmentLayer, 'heat');
  assert.deepEqual([hottestNow.value, hottestNow.name, hottestNow.peak], [35, 'อำเภอ ข', 39], 'National heat is the district hottest now, with its own peak');
  assert.equal(highestEnvironmentValue({ features: [{ properties: { pm25_24h: null } }] }, 'air_quality'), null, 'Null is not zero');
  assert.equal(highestEnvironmentValue({ features: [environmentLayer.features[2]] }, 'air_quality').value, 0, 'A measured zero remains valid');
  const metricArea0 = () => ({ area: { name_th: 'พื้นที่เลือก', parents: ['จังหวัด'] }, data_updated_at: '2026-11-04T05:00:00Z', degraded_services: [] });
  const metricProps = { layers: { air_quality: { data: environmentLayer, error: false, key: 'air_quality:0' }, heat: { data: environmentLayer, error: false, key: 'heat:0' }, rain_storm: { data: environmentLayer, error: false, key: 'rain_storm:0' } }, loadingArea: false, area: null };
  let metrics = instance(EnvironmentSummary).render(metricProps);
  assert(content(metrics).includes('80') && content(metrics).includes('เชียงใหม่'), 'National PM2.5 maximum shows value and place');
  assert(content(metrics).includes('ตอนนี้ร้อนเหมือน 35') && content(metrics).includes('ค่าคาดการณ์ชั่วโมงนี้'), 'National heat shows current-hour feel temperature and period');
  assert(content(metrics).includes('สงขลา') && content(metrics).includes('สูงสุดใน 24 ชม. ข้างหน้า 39'), 'National heat shows place and the 24-h peak of that district');
  assert(nodes(metrics).some(n => n.props.className === 'risk-badge level-NORMAL'), 'API level wins even when value exceeds a frontend threshold');
  const rainCard = nodes(metrics).filter(n => n.type === 'article')[2];
  assert(content(rainCard).includes('โอกาสฝนตก') && content(rainCard).includes('63') && content(rainCard).includes('ราว 18:00 น.') && content(rainCard).includes('แบบจำลอง'), 'Rain card shows the highest chance, its hour, and says it is a model');
  const rainBadge = nodes(rainCard).find(n => String(n.props.className).startsWith('risk-badge'));
  assert(content(rainBadge).includes('ระดับภัยฝนและพายุ (จากปริมาณฝน): ปกติ'), 'Rain card level says it is the rain/storm level, not a level for the chance of rain');
  const { ForecastNote, forecastSeries } = load('@/components/RiverPanel');
  const riverForecast = { station_id: '2744', available: true, from_time: '2026-11-05 12:00', level_msl: 14.0, bank_msl: 16.0,
    points: [{ hours: 6, time: '2026-11-05 18:00', level_msl: 14.35, change_m: 0.35, above_bank_m: -1.65, test_mae_m: 0.08 },
             { hours: 24, time: '2026-11-06 12:00', level_msl: 16.2, change_m: 2.2, above_bank_m: 0.2, test_mae_m: 0.123 }] };
  const note = content(ForecastNote({ forecast: riverForecast }));
  assert(note.includes('ไม่ใช่ระดับเตือนภัย') && note.includes('14.35') && note.includes('ขึ้น 35') && note.includes('ต่ำกว่าตลิ่ง 1.65') && note.includes('สูงกว่าตลิ่ง 0.20') && note.includes('8–12 ซม.'),
    'River forecast says it is a model, not a warning level, with each level, change, bank distance and test error');
  assert.equal(forecastSeries(riverForecast).length, 3, 'The dashed line starts at the latest reading and runs through each forecast hour');
  assert.deepEqual(forecastSeries(riverForecast, [1, 13.9])[0], [1, 13.9], 'The dashed line continues from the last point of the level line');
  assert(note.includes('ราว ') && note.includes('18น.') && !note.includes('อีก 6'), 'Forecast hours are clock times, not "in N hours" counted from the start of the hour');
  assert(content(ForecastNote({ forecast: { station_id: '1', available: false, reason: 'not_chosen' } })).includes('ไม่แม่นกว่า'), 'A station the model was not chosen for says why there is no forecast');
  assert.equal(ForecastNote({ forecast: { station_id: '1', available: false, reason: 'no_model' } }), null, 'No model: nothing is shown');
  assert.equal(forecastSeries(null).length, 0, 'No forecast: no dashed line');
  metrics = instance(EnvironmentSummary).render({ ...metricProps, area: { ...metricArea0(), assessments: [{ hazard: 'rain_storm', level: 'NORMAL', observations: { rain_chance_max_24h: 25, rain_chance_peak_hour: 7 }, sources: [] }] } });
  assert(content(nodes(metrics).filter(n => n.type === 'article').find(n => content(n).includes('25'))).includes('25') && content(metrics).includes('ราว 07:00 น.') && !content(metrics).includes('63'), 'Selected area shows its own chance of rain and hour');
  const metricArea = { area: { name_th: 'พื้นที่เลือก', parents: ['จังหวัด'] }, assessments: [{ hazard: 'heat', level: 'WATCH', observations: { heat_index_now: 27, heat_index_max_24h: 30 }, sources: ['Open-Meteo'] }], data_updated_at: '2026-11-04T05:00:00Z', degraded_services: [] };
  metrics = instance(EnvironmentSummary).render({ ...metricProps, area: metricArea });
  assert(content(metrics).includes('27') && content(metrics).includes('30') && !content(metrics).includes('35'), 'Selected area shows its own now and peak, never the national one');
  assert(nodes(metrics).some(n => n.props.className === 'risk-badge level-WATCH'), 'Selected area keeps API assessment level');
  metrics = instance(EnvironmentSummary).render({ ...metricProps, area: metricArea, loadingArea: true });
  assert(!content(metrics).includes('27') && !content(metrics).includes('30'), 'Loading another area hides old values');
  metrics = instance(EnvironmentSummary).render({ ...metricProps, layers: { air_quality: { data: null, error: true }, heat: { data: null, error: false } } });
  assert(content(metrics).includes('โหลดข้อมูลไม่ได้') && content(metrics).includes('กำลังโหลด'), 'Independent failure and loading states');
  const heatPending = { ...environmentLayer, meta: { ...environmentLayer.meta, inputs_pending: ['heat_model', 'marine_model'] },
    features: environmentLayer.features.map(f => ({ properties: { ...f.properties, heat_index_now: null, heat_index_max_24h: null } })) };
  metrics = instance(EnvironmentSummary).render({ ...metricProps, layers: { air_quality: { data: { ...environmentLayer, meta: { ...environmentLayer.meta, inputs_pending: ['marine_model'] } }, error: false }, heat: { data: heatPending, error: false } } });
  const [airCard, heatCard] = nodes(metrics).filter(n => n.type === 'article');
  assert(content(heatCard).includes('กำลังโหลด') && !content(heatCard).includes('ไม่มีข้อมูล'), 'A heat forecast still loading reads as loading, not as no data');
  assert(content(heatCard).includes('ข้อมูลยังไม่ครบ'), 'The heat card says its own forecast is incomplete');
  assert(content(airCard).includes('80') && !content(airCard).includes('ข้อมูลยังไม่ครบ'), 'Another input still loading does not mark the PM2.5 card incomplete');
  metrics = instance(EnvironmentSummary).render({ ...metricProps, area: { ...metricArea, assessments: [{ hazard: 'air_quality', level: 'NORMAL', observations: { pm25_24h: null, pm25_model_24h: 12 }, sources: ['Open-Meteo / CAMS (แบบจำลอง)'] }] } });
  assert(content(metrics).includes('12') && content(metrics).includes('แบบจำลอง') && content(metrics).includes('CAMS'), 'Selected model fallback is labelled and credited');
  load('@/lib/i18n').setLanguage('en');
  metrics = instance(EnvironmentSummary).render(metricProps);
  assert(content(metrics).includes('Feels like now 35') && content(metrics).includes('Forecast for this hour') && nodes(metrics).some(n => n.type === 'article' && n.props.title.includes('National maximum')), 'Summary copy translates without changing readings');
  assert(nodes(metrics).some(n => n.type === 'article' && n.props.title.includes('· Maximum over the next 24 hours')), 'The 24-hour heat peak is translated in English');
  const rainCardEn = content(nodes(metrics).filter(n => n.type === 'article')[2]);
  assert(rainCardEn.includes('highest in 24 h, around 18:00') && !/[฀-๿]/.test(rainCardEn.replace(/อำเภอ ก|เชียงใหม่/g, '')), 'Rain card hour and period translate in English');
  load('@/lib/i18n').setLanguage('th');
  const { areaWarnings } = load('@/lib/area-warnings');
  const notice = { title: 'ต้นฉบับประกาศ', hazards: ['rain_storm', 'flood'], provinces: ['TH50', 'TH90'],
    provinces_by_hazard: { rain_storm: ['TH50'], flood: ['TH90'] },
    impact_from: '2026-11-05', impact_to: '2026-11-09',
    impact_by_hazard: { rain_storm: { TH50: ['2026-11-05', '2026-11-07'] } } };
  const notices = (w, code = 'TH5001', hazard = 'overview', today = '2026-11-04') => areaWarnings([w], code, hazard, today);
  assert.equal(notices(notice, 'TH1301').length, 0, 'Warnings from other provinces are hidden');
  assert.equal(notices(notice, 'TH5001', 'flood').length, 0, 'Hazard-specific areas take precedence over all provinces');
  assert.deepEqual(notices(notice)[0].impacts, [{ hazard: 'rain_storm', from: '2026-11-05', to: '2026-11-07' }]);
  assert.equal(notices(notice, 'TH50', 'rain_storm').length, 1, 'Province selection uses its own code');
  assert.equal(notices(notice, 'TH5001', 'overview', '2026-11-08').length, 0, 'Expired provincial periods are hidden');
  assert.equal(notices({ ...notice, provinces_by_hazard: { rain_storm: [] } }).length, 0, 'Empty areas never fall back to all provinces');
  assert.equal(notices({ ...notice, provinces_by_hazard: undefined, impact_by_hazard: undefined })[0].impacts[0].to, '2026-11-09', 'Legacy warnings use whole impact dates');
  const periods = { ...notice, periods_by_hazard: { rain_storm: { TH50: [['2026-11-05', '2026-11-06', false], ['2026-11-08', '2026-11-09', 'heavy_rain']] } } };
  assert.deepEqual(notices(periods, 'TH5001', 'rain_storm', '2026-11-07')[0].impacts, [{ hazard: 'rain_storm', from: '2026-11-08', to: '2026-11-09' }], 'New period dates override merged legacy dates');
  const undated = { title: 'ไม่ระบุวัน', hazards: ['flood'], provinces: ['TH50'] };
  assert.deepEqual(notices(undated)[0].impacts, [{ hazard: 'flood', from: null, to: null }]);
  const StormsPanel = load('@/components/StormsPanel').default;
  let stormResponse = { storms: [{ name: 'CHAMPI', time: '2026-10-22T12:00:00+00:00', lat: 21.2, lon: 124.9, wind_kmh: 185, category: { category: 'Cat 3' } }], source: 'สสน.', degraded_services: [] };
  lib.api.storms = async () => stormResponse;
  const stormEffects = [], stormApp = instance(StormsPanel, stormEffects);
  assert(content(stormApp.render({ revision: 0 })).includes('กำลังโหลด'), 'Storm panel has a loading state');
  let cleanupStorm = stormEffects[0](); await tick();
  let stormTree = stormApp.render({ revision: 0 });
  assert(content(stormTree).includes('CHAMPI') && content(stormTree).includes('185') && content(stormTree).includes('Cat 3'));
  assert(content(stormTree).includes('19:00') || content(stormTree).includes('19.00'), 'UTC storm times show Bangkok time');
  assert(content(stormTree).includes('21.2') && content(stormTree).includes('124.9'), 'Storm coordinates are visible');
  cleanupStorm?.();
  stormResponse = { storms: [], source: 'สสน.', degraded_services: [], inputs_pending: ['thaiwater_storms'] };
  stormApp.render({ revision: 1 }); cleanupStorm = stormEffects.at(-1)(); await tick();
  assert(!content(stormApp.render({ revision: 1 })).includes('ไม่มีพายุ'), 'Pending data must not claim no storms'); cleanupStorm?.();
  stormResponse = { storms: [], source: 'สสน.', degraded_services: [] };
  stormApp.render({ revision: 2 }); cleanupStorm = stormEffects.at(-1)(); await tick();
  assert(content(stormApp.render({ revision: 2 })).includes('ไม่มีพายุ')); cleanupStorm?.();
  for (const metadata of [
    { inputs_pending: ['air_model', 'heat_model', 'marine_model'], degraded_services: [] },
    { inputs_pending: [], degraded_services: ['nasa_firms'] },
  ]) {
    stormResponse = { storms: [], source: 'สสน.', ...metadata };
    const effects = [], panel = instance(StormsPanel, effects);
    panel.render({ revision: 0 }); const cleanup = effects[0](); await tick();
    const tree = panel.render({ revision: 0 });
    assert(content(tree).includes('ไม่มีพายุ'), 'Unrelated sources do not hide a successful empty storm list');
    assert(!content(tree).includes('กำลังรอข้อมูลพายุ') && !content(tree).includes('ข้อมูลพายุบางแหล่งไม่พร้อมใช้งาน'));
    assert.equal(tree.props['aria-busy'], false, 'Unrelated pending inputs do not mark storms busy'); cleanup?.();
  }
  stormResponse = { storms: [], source: 'สสน.', degraded_services: ['thaiwater_storms'] };
  stormApp.render({ revision: 5 }); cleanupStorm = stormEffects.at(-1)(); await tick();
  stormTree = stormApp.render({ revision: 5 });
  assert(content(stormTree).includes('ข้อมูลพายุบางแหล่งไม่พร้อมใช้งาน') && !content(stormTree).includes('ไม่มีพายุ'), 'Storm source failures still show degraded data'); cleanupStorm?.();
  lib.api.storms = async () => { throw new Error('offline'); };
  stormApp.render({ revision: 3 }); cleanupStorm = stormEffects.at(-1)(); await tick();
  assert(content(stormApp.render({ revision: 3 })).includes('โหลดรายชื่อพายุไม่สำเร็จ')); cleanupStorm?.();
  lib.api.storms = async () => ({ storms: [{ name: 'EMPTY', time: '2026-11-04T00:00:00Z', wind_kmh: null, lat: null, lon: null, category: null }], source: 'สสน.' });
  stormApp.render({ revision: 4 }); cleanupStorm = stormEffects.at(-1)(); await tick();
  assert(content(stormApp.render({ revision: 4 })).includes('ความเร็วลม: —'), 'Missing wind is not rendered as zero'); cleanupStorm?.();
  const AreaWarnings = load('@/components/AreaWarnings').default;
  const warningTree = AreaWarnings({ warnings: [{ ...notice, impact_from: '2999-11-05', impact_to: '2999-11-09', impact_by_hazard: undefined }], code: 'TH5001', hazard: 'overview', assessments: [{ hazard: 'rain_storm', level: 'CRITICAL', rule_ids: ['OFFICIAL_WARNING_UPCOMING'] }] });
  assert(content(warningTree).includes(notice.title), 'The original bulletin title is preserved');
  assert(!content(warningTree).includes('น้ำท่วม'), 'Overview only shows hazards applicable to the chosen province');
  assert(nodes(warningTree).some(n => n.props.className === 'risk-badge level-CRITICAL'), 'Area warning badge uses the API assessment level');
  assert(!content(warningTree).includes('ประกาศระดับประเทศ'), 'Selected-area warning no longer claims a nationwide notice');
  lib.api.search = async () => { observed.search++; return [{ code: '13', level: 'province', name_th: 'ปทุมธานี', parents: [], lat: 14, lon: 100 }]; };
  lib.api.area = async () => ({ area: { code: '13', level: 'province', name_th: 'ปทุมธานี', parents: [] }, assessments: [], forecast: [], rivers: [{ status: 'NORMAL' }], river_province: { code: '13', name_th: 'ปทุมธานี' }, official_warnings: [], degraded_services: [] });
  lib.api.boundary = async () => null;
  const Dashboard = load('@/components/Dashboard').default;
  // Search a province, tap districts, then step back without creating a history loop.
  {
    const saved = { area: lib.api.area, search: lib.api.search, boundary: lib.api.boundary };
    const places = {
      TH20: { code: 'TH20', level: 'province', name_th: 'ชลบุรี', name_en: '', parents: [], lat: 13.3, lon: 100.9 },
      TH2007: { code: 'TH2007', level: 'amphoe', name_th: 'ศรีราชา', name_en: '', parents: ['ชลบุรี'], lat: 13.1, lon: 100.9 },
      TH2001: { code: 'TH2001', level: 'amphoe', name_th: 'เมืองชลบุรี', name_en: '', parents: ['ชลบุรี'], lat: 13.4, lon: 101 },
    };
    const boundary = { type: 'FeatureCollection', features: [] };
    lib.api.search = async () => [places.TH20];
    lib.api.boundary = async () => boundary;
    lib.api.area = async code => ({ area: places[code], assessments: [], forecast: [], rivers: [], official_warnings: [], degraded_services: [] });
    try {
      const historyApp = instance(Dashboard);
      const hp = { activeView: 'hazard', activeHazard: 'overview', searchInHeader: true, searchTarget: {}, locationTarget: {} };
      const map = t => nodes(t).find(n => n.props.mode === 'hazard' && 'dark' in n.props);
      const back = t => nodes(t).find(n => n.props['aria-label'] === 'ย้อนกลับพื้นที่ก่อนหน้า');
      let t = historyApp.render(hp);
      nodes(t).find(n => n.props.id === 'area-search').props.onChange({ target: { value: 'ชลบุรี' } });
      t = historyApp.render(hp);
      await nodes(t).find(n => n.props.role === 'search').props.onSubmit({ preventDefault() {} });
      t = historyApp.render(hp);
      nodes(t).find(n => n.props.role === 'option').props.onClick(); await tick();
      t = historyApp.render(hp);
      assert(back(t), 'Selecting a province offers a back button');
      map(t).props.onSelect('TH2007'); await tick(); t = historyApp.render(hp);
      map(t).props.onSelect('TH2007'); await tick(); t = historyApp.render(hp);
      map(t).props.onSelect('TH2001'); await tick(); t = historyApp.render(hp);
      back(t).props.onClick(); await tick(); t = historyApp.render(hp);
      assert.equal(map(t).props.selected, 'TH2007', 'Back restores the previous district');
      back(t).props.onClick(); await tick(); t = historyApp.render(hp);
      assert.equal(map(t).props.selected, null, 'Back restores the province rather than duplicating repeated taps');
      assert.equal(map(t).props.outline, boundary, 'Province outline is restored');
      assert(content(t).includes('ชลบุรี'), 'Province label is restored');
      back(t).props.onClick(); await tick(); t = historyApp.render(hp);
      assert.equal(map(t).props.selected, null);
      assert.equal(map(t).props.outline, null);
      assert(!back(t), 'Returning to the country exhausts history');
    } finally { Object.assign(lib.api, saved); }
  }
  assert(!nodes(instance(Dashboard).render({ activeView: "hazard", activeHazard: "overview" })).some(n => n.props.className === "map-supplement"), "Overview removes the entire map supplement accordion");
  assert(!nodes(instance(Dashboard).render({ activeView: 'hazard', activeHazard: 'overview' })).some(n => n.props.className === 'map-summary'), 'Overview omits the redundant risk legend and timestamp');
  assert(nodes(instance(Dashboard).render({ activeView: 'hazard', activeHazard: 'flood' })).some(n => n.props.className === 'map-summary'), 'Individual hazard maps retain their risk legend');
  const environmentRequests = [];
  lib.api.layer = async id => { environmentRequests.push(id); return environmentLayer; };
  const environmentEffects = [];
  const environmentApp = instance(Dashboard, environmentEffects);
  environmentApp.render({ activeHazard: 'overview' });
  const refreshEnvironment = environmentEffects.find(effect => /setEnvironmentLayers/.test(String(effect)));
  assert.deepEqual(refreshEnvironment.dependencies, [0], 'Environment requests depend only on refresh revision, not tab or area');
  const stopEnvironment = refreshEnvironment();
  await tick();
  assert.deepEqual(environmentRequests, ['air_quality', 'heat', 'rain_storm'], 'Load the three summaries once per refresh');
  for (const activeHazard of ['heat', 'air_quality', 'rain_storm']) {
    environmentEffects.length = 0;
    environmentApp.render({ activeHazard });
    environmentEffects.find(effect => /setLayerState/.test(String(effect)))();
  }
  assert.deepEqual(environmentRequests, ['air_quality', 'heat', 'rain_storm'], 'Map tabs reuse the summary layers without another request');
  stopEnvironment();
  const originalArea = lib.api.area;
  let areaReading = 12, areaCalls = 0, delayedArea;
  lib.api.area = async code => {
    areaCalls++;
    const data = { area: { code, level: 'amphoe', name_th: code, parents: [] }, assessments: [
      { hazard: 'air_quality', level: areaReading === 12 ? 'NORMAL' : 'WARNING', observations: { pm25_24h: areaReading }, sources: [], notices: [], rule_ids: [] },
      { hazard: 'heat', level: areaReading === 12 ? 'NORMAL' : 'WATCH', observations: { heat_index_max_24h: areaReading + 20 }, sources: [], notices: [], rule_ids: [] },
    ], forecast: [], rivers: [], official_warnings: [], degraded_services: [] };
    return delayedArea ? new Promise(resolve => { delayedArea = () => resolve(data); }) : data;
  };
  const refreshEffects = [], refreshApp = instance(Dashboard, refreshEffects);
  const refreshProps = { activeView: 'hazard', activeHazard: 'overview' };
  let refreshTree = refreshApp.render(refreshProps);
  const mapOf = tree => nodes(tree).find(n => n.props.mode === 'hazard' && 'dark' in n.props);
  const summaryOf = tree => nodes(tree).find(n => n.type === EnvironmentSummary).props;
  const refreshArea = () => {
    refreshEffects.length = 0; refreshTree = refreshApp.render(refreshProps);
    const effect = refreshEffects.find(e => /api\.area/.test(String(e)));
    assert(effect, 'Selected areas have a refresh effect'); return effect();
  };
  mapOf(refreshTree).props.onSelect('TH1001'); await tick();
  refreshTree = refreshApp.render(refreshProps);
  assert.equal(summaryOf(refreshTree).area.assessments[0].observations.pm25_24h, 12);
  areaReading = 40;
  nodes(refreshTree).find(n => n.props['aria-label'] === 'โหลดข้อมูลแผนที่ใหม่').props.onClick();
  let stopAreaRefresh = refreshArea(); await tick(); refreshTree = refreshApp.render(refreshProps);
  assert.equal(areaCalls, 2, 'Refresh reloads the selected area once');
  assert.deepEqual(summaryOf(refreshTree).area.assessments.map(a => a.level), ['WARNING', 'WATCH']);
  assert.deepEqual(summaryOf(refreshTree).area.assessments.map(a => Object.values(a.observations)[0]), [40, 60]);
  stopAreaRefresh?.(); delayedArea = true;
  stopAreaRefresh = refreshArea(); await tick();
  const resolveOldArea = delayedArea; delayedArea = false;
  refreshTree = refreshApp.render(refreshProps); mapOf(refreshTree).props.onSelect('TH1002'); await tick();
  resolveOldArea(); await tick(); refreshTree = refreshApp.render(refreshProps);
  assert.equal(summaryOf(refreshTree).area.area.code, 'TH1002', 'Old refresh cannot overwrite a new selection'); stopAreaRefresh?.();
  delayedArea = true; stopAreaRefresh = refreshArea(); await tick();
  const resolveClearedArea = delayedArea; delayedArea = false;
  refreshTree = refreshApp.render(refreshProps);
  nodes(refreshTree).find(n => n.props.className?.includes('area-reset')).props.onClick();
  resolveClearedArea(); await tick(); refreshTree = refreshApp.render(refreshProps);
  assert.equal(summaryOf(refreshTree).area, null, 'Old refresh cannot restore a cleared selection'); stopAreaRefresh?.();
  delayedArea = true;
  mapOf(refreshTree).props.onSelect('TH1003'); await tick();
  const resolveInitialArea = delayedArea; delayedArea = false;
  stopAreaRefresh = refreshArea(); await tick(); resolveInitialArea(); await tick();
  const refreshedPlace = refreshApp.render({ ...refreshProps, activeView: 'rivers' });
  assert(!content(refreshedPlace).includes('กำลังโหลดพื้นที่…'), 'Refresh during initial selection also fills the place name'); stopAreaRefresh?.();
  lib.api.area = originalArea;
  for (const activeHazard of ['overview', 'earthquake', 'tropical_cyclone']) {
    const tree = instance(Dashboard).render({ activeView: 'hazard', activeHazard });
    assert.equal(nodes(tree).filter(n => n.type === StormsPanel).length, activeHazard === 'tropical_cyclone' ? 1 : 0, 'HII storms mount only on the cyclone tab');
  }
  let radarRequests = 0;
  let radarEnabled = true;
  let radarFrames = [{ tiles: 'https://tiles.test/old/{z}/{x}/{y}.png', time: 1799996400 }, { tiles: 'https://tiles.test/new/{z}/{x}/{y}.png', time: 1800000000 }];
  lib.api.radar = async () => ({ enabled: radarEnabled, frames: radarEnabled ? radarFrames : [] });
  lib.api.radarNowcast = async () => { radarRequests++; return {
    enabled: radarEnabled,
    status: radarEnabled ? 'ok' : 'unavailable',
    past_frames: radarEnabled ? radarFrames.map(frame => ({ tile_url: frame.tiles, time: frame.time })) : [],
    forecast_frames: radarEnabled ? [
      { time: radarFrames.at(-1).time + 600, horizon_min: 10, dx_pixels: 1.5, dy_pixels: -0.5, cells: [{ id: 'cell-1', lat: 13.52, lon: 100.61 }] },
      { time: radarFrames.at(-1).time + 1200, horizon_min: 20, dx_pixels: 3, dy_pixels: -1, cells: [{ id: 'cell-1', lat: 13.54, lon: 100.72 }] },
    ] : [],
    motion: radarEnabled ? { speed_kmh: 36, direction_deg: 90, direction_th: 'ตะวันออก', is_moving: true } : null,
    rain_cells: radarEnabled ? [{ id: 'cell-1', centroid_lat: 13.5, centroid_lon: 100.5, peak_intensity_dbz: 42 }] : [],
    explanation: 'กลุ่มฝนเดิมกำลังเคลื่อนไปทางตะวันออก',
    explanation_source: 'qwen',
  }; };
  for (const hazard of ['overview', 'flood', 'rain_storm', 'tropical_cyclone']) {
    const radarEffects = [];
    const radarApp = instance(Dashboard, radarEffects);
    const props = { activeView: 'hazard', activeHazard: hazard };
    const radarTree = radarApp.render(props);
    const before = radarRequests;
    const cleanupRadar = radarEffects.find(effect => /\.radarNowcast\(/.test(String(effect)))();
    assert.equal(radarRequests - before, ['overview', 'rain_storm', 'tropical_cyclone'].includes(hazard) ? 1 : 0, `Only overview and weather topics request radar: ${hazard}`);
    if (hazard === 'overview') {
      assert(!nodes(radarTree).some(n => n.props['data-layer'] === 'radar'), 'Radar stays hidden while availability is unknown');
      await tick();
      let enabledTree = radarApp.render(props);
      enabledTree = radarApp.render(props);
      assert(nodes(enabledTree).some(n => n.props['data-layer'] === 'radar'), 'Overview radar toggle appears when backend enables it');
      assert(nodes(enabledTree).filter(n => ['pm25', 'radar'].includes(n.props['data-layer'])).every(n => n.props['aria-pressed'] === false), 'A fresh homepage starts with optional layers off');
      nodes(enabledTree).find(n => n.props['data-layer'] === 'radar').props.onClick();
      enabledTree = radarApp.render(props);

      assert.equal(nodes(enabledTree).find(n => 'radarNext' in n.props).props.radar, radarFrames.at(-1).tiles, 'The first render with frames already draws the latest frame, not the oldest');
      const playback = nodes(enabledTree).find(n => n.type.name === 'RadarPlayback');
      assert(playback && playback.props.frames.length === 4 && playback.props.index === 1, 'Radar playback appends forecasts and opens on the latest observed frame');
      assert.equal(playback.props.frames[2].kind, 'forecast', 'Forecast entries follow observed radar frames in one timeline');
      assert.equal(playback.props.frames[2].horizonMin, 10, 'Forecast entry retains its lead time');
      for (const riskLevel of ['CRITICAL', 'WARNING', 'WATCH']) {
        const filtered = radarApp.render({ ...props, riskLevel });
        assert(!nodes(filtered).some(n => n.type.name === 'RadarPlayback'), `The radar playback bar is hidden for ${riskLevel}`);
        assert.equal(nodes(filtered).find(n => 'radarNext' in n.props).props.radar, null, 'Filtered risk maps do not show radar');
        assert.equal(nodes(filtered).find(n => n.props['data-layer'] === 'radar').props['aria-pressed'], true, 'Filtering preserves the radar toggle');
      }
      enabledTree = radarApp.render(props);
      assert(nodes(enabledTree).some(n => n.type.name === 'RadarPlayback'), 'Returning to overview restores the radar bar');


      assert.equal(nodes(enabledTree).find(n => 'radarNext' in n.props).props.radarNext, null, 'Idle radar playback does not preload another frame');
      const playingTree = radarApp.render(props);
      assert.equal(nodes(playingTree).find(n => 'radarNext' in n.props).props.radarNext, null, 'Forecast entries do not preload a radar image');
      radarApp.render(props);
      playback.props.onChange(2);
      const forecastTree = radarApp.render(props);
      const forecastMap = nodes(forecastTree).find(n => 'radarForecast' in n.props);
      assert.equal(forecastMap.props.radarForecast.features.length, 2, 'Forecast shows the projected path and position for each rain cell');
      const projected = forecastMap.props.radarForecast.features[1].geometry.coordinates;
      assert.deepEqual(projected, [100.61, 13.52], 'The map draws the position the backend projected for this lead time');
      assert.equal(forecastMap.props.radar, radarFrames.at(-1).tiles, 'Forecast keeps the latest observed source without reloading it');
      assert.equal(forecastMap.props.radarDimmed, true, 'The observed image is clearly dimmed during forecasts');
      assert.equal(playback.props.frames[2].imageTime, radarFrames.at(-1).time, 'Forecast retains the actual image time');
      const forecastNote = nodes(forecastTree).find(n => String(n.props.className || '').split(/\s+/).includes('radar-note'));
      assert.equal(forecastNote, undefined, 'The map hides the observed-radar note while a forecast is selected');
      const RadarPlayback = load('@/components/RadarPlayback').default;
      const forecastPlayback = instance(RadarPlayback).render(nodes(forecastTree).find(n => n.type.name === 'RadarPlayback').props);
      assert(content(forecastPlayback).includes('ค่าคาดการณ์จากการคำนวณทิศทางการเคลื่อนที่ของกลุ่มฝน'), 'Forecast is attributed to motion calculations');
      assert(content(forecastPlayback).includes('อธิบายโดย AI ที่รันในเครื่องของทันภัย'), 'Qwen is credited with explanation only');
      assert(content(forecastPlayback).includes('ค่าคาดการณ์ +10 นาที'), 'The selected forecast stays visibly labelled in the playback controls');
      assert(content(forecastPlayback).includes('กลุ่มฝนเดิมกำลังเคลื่อนไปทางตะวันออก'), 'The validated Qwen explanation accompanies its frame');
      assert(content(forecastPlayback).includes('ภาพคาดการณ์คือภาพเรดาร์จริงเวลา') && content(forecastPlayback).includes('ไม่รวมฝนที่เกิดใหม่หรือสลายไป'), 'The moved image is labelled as an observed image shifted by the motion');
      assert(content(forecastPlayback).includes('ไม่ทำนายฝนที่เกิดใหม่'), 'The forecast limitation stays next to its timeline');
      assert(!content(forecastPlayback).includes('AI กำลังเรียบเรียง'), 'No waiting notice unless the backend says Qwen is still writing');
      const pendingPlayback = instance(RadarPlayback).render({ ...nodes(forecastTree).find(n => n.type.name === 'RadarPlayback').props, explanationSource: 'template', explanationPending: true });
      assert(content(pendingPlayback).includes('AI กำลังเรียบเรียงคำอธิบาย'), 'A pending Qwen text is announced');
      const templatePlayback = instance(RadarPlayback).render({ ...nodes(forecastTree).find(n => n.type.name === 'RadarPlayback').props, explanationSource: 'template', explanationPending: false });
      assert(!content(templatePlayback).includes('AI กำลังเรียบเรียง'), 'A final template answer (Qwen off, failed validation, no direction) has no waiting notice');
      load('@/lib/i18n').setLanguage('en');
      const englishPlayback = instance(RadarPlayback).render({ ...nodes(forecastTree).find(n => n.type.name === 'RadarPlayback').props, explanation: 'กลุ่มฝนเดิมกำลังเคลื่อนไปทางตะวันออก' });
      assert(nodes(englishPlayback).some(n => n.type === 'p' && n.props.children === 'กลุ่มฝนเดิมกำลังเคลื่อนไปทางตะวันออก'), 'The validated explanation is shown exactly, not re-translated');
      assert.equal(englishPlayback.props['aria-label'], "Play the past hour of radar images and the next hour's forecast", 'The playback label is read in English');
      load('@/lib/i18n').setLanguage('th');

      const { radarImageLoaded, radarImageFailed } = load('@/components/RadarPlayback');
      const stepProbe = instance(RadarPlayback).render({ frames: [{ time: 0 }, { time: 600 }], index: 0, onChange() {} });
      assert.equal(nodes(stepProbe).find(n => n.type === 'input').props.step, 'any', 'The timeline can be dragged continuously');
      let animationId = 0, clock = 0;
      const animations = new Map();
      window.requestAnimationFrame = callback => { animations.set(++animationId, callback); return animationId; };
      window.cancelAnimationFrame = id => animations.delete(id);
      document.addEventListener = () => {};
      document.removeEventListener = () => {};
      // One fake clock for animation frames, performance.now() and the preload interval.
      const intervals = new Map();
      let intervalId = 0;
      window.setInterval = (fn, ms) => { intervals.set(++intervalId, { fn, ms, next: clock + ms }); return intervalId; };
      window.clearInterval = id => intervals.delete(id);
      const savedPerformanceNow = performance.now;
      performance.now = () => clock;
      const advance = ms => {
        const end = clock + ms;
        for (const timer of [...intervals.values()]) while (timer.next <= end) { clock = timer.next; timer.next += timer.ms; timer.fn(); }
        clock = end;
        const pending = [...animations.values()]; animations.clear(); pending.forEach(callback => callback(clock));
      };
      // The map has these images already, so this playback never waits.
      radarFrames.forEach(f => f.tiles && radarImageLoaded(f.tiles));
      let selectedFrame = 0, mapUpdates = 0;
      const playbackApp = instance(RadarPlayback, undefined, true);
      const playbackProps = () => ({ frames: radarFrames, index: selectedFrame, onChange: index => { selectedFrame = index; mapUpdates++; } });
      let movingTree = playbackApp.render(playbackProps());
      nodes(movingTree).find(n => n.type === 'button').props.onClick();
      playbackApp.render(playbackProps());
      advance(0); advance(6000);
      movingTree = playbackApp.render(playbackProps());
      assert.equal(nodes(movingTree).find(n => n.type === 'input').props.value, 0.5, 'The slider moves between frames');
      assert.equal(mapUpdates, 0, 'Smooth slider animation does not redraw the map between images');
      advance(6000);
      movingTree = playbackApp.render(playbackProps());
      assert.equal(selectedFrame, 1);
      assert.equal(mapUpdates, 1, 'The map still changes only once per radar image');
      nodes(movingTree).find(n => n.type === 'input').props.onChange({ target: { value: '0.37' } });
      movingTree = playbackApp.render(playbackProps());
      assert.equal(nodes(movingTree).find(n => n.type === 'input').props.value, 0.37, 'Dragging does not snap the thumb to an image');
      assert.equal(animations.size, 0);

      const currentFrames = [
        { kind: 'forecast', time: 400 },
        { kind: 'observed', time: 300 },
        { kind: 'observed', time: 100 },
      ];
      let currentSelection = 2;
      const currentApp = instance(RadarPlayback, undefined, true);
      const currentProps = () => ({ frames: currentFrames, index: currentSelection, onChange: i => { currentSelection = i; } });
      let currentTree = currentApp.render(currentProps());
      nodes(currentTree).find(n => n.type === 'button').props.onClick();
      currentTree = currentApp.render(currentProps());
      assert.equal(animations.size, 1, 'Play starts the animation');
      const currentButton = nodes(currentTree).find(n => n.type === 'button' && n.props.className === 'radar-current-button');
      assert(currentButton, 'Radar has a current-time button');
      currentButton.props.onClick();
      currentApp.render(currentProps());
      assert.equal(currentSelection, 1, 'Current time selects the newest observed timestamp, excluding forecasts');
      assert.equal(animations.size, 0, 'Returning to current time stops playback');

      const speedApp = instance(RadarPlayback, undefined, true);
      let speedTree = speedApp.render(currentProps());
      const speedButton = label => nodes(speedTree).find(n => n.type === 'button' && n.props['aria-label'] === label);
      for (const speed of [3, 5]) {
        speedButton('เพิ่มความเร็ว').props.onClick(); speedTree = speedApp.render(currentProps());
        assert.equal(content(nodes(speedTree).find(n => n.type === 'output' && n.props['aria-live'])), speed + '×');
      }
      assert.equal(speedButton('เพิ่มความเร็ว').props.disabled, true);
      nodes(speedTree).find(n => n.type === 'input').props.onChange({ target: { value: '0' } });
      speedTree = speedApp.render(currentProps());
      nodes(speedTree).find(n => n.type === 'button').props.onClick(); speedApp.render(currentProps());
      advance(0); advance(600);
      speedTree = speedApp.render(currentProps());
      assert.equal(nodes(speedTree).find(n => n.type === 'input').props.value, 0.25, '5x keeps continuous progress');
      for (const speed of [3, 1]) {
        speedButton('ลดความเร็ว').props.onClick(); speedTree = speedApp.render(currentProps());
        assert.equal(content(nodes(speedTree).find(n => n.type === 'output' && n.props['aria-live'])), speed + '×');
        assert.equal(nodes(speedTree).find(n => n.type === 'input').props.value, 0.25, 'Speed changes preserve the thumb position');
        assert.equal(animations.size, 1);
      }
      assert.equal(speedButton('ลดความเร็ว').props.disabled, true);
      nodes(speedTree).find(n => n.type === 'button').props.onClick(); speedTree = speedApp.render(currentProps());
      assert.equal(animations.size, 0);

      // RainViewer quota: one new image per 12 s, fetched ahead by the map's hidden layer even while paused.
      // Playback waits until the map reports the image loaded (not merely requested), and a failed or stuck
      // fetch is tried again on a later turn.
      const quotaFrames = [{ kind: 'observed', time: 0, tiles: 'a' }, { kind: 'observed', time: 600, tiles: 'b' }, { kind: 'observed', time: 1200, tiles: 'c' }];
      let quotaSelection = 0;
      const preloads = [];
      const quotaApp = instance(RadarPlayback, undefined, true);
      const quotaProps = () => ({ frames: quotaFrames, index: quotaSelection, onChange: i => { quotaSelection = i; }, onPreload: tiles => preloads.push(tiles) });
      const quotaStart = clock;
      let quotaTree = quotaApp.render(quotaProps());
      const runUntil = ms => { while (clock - quotaStart < ms) { advance(100); quotaTree = quotaApp.render(quotaProps()); } };
      const loadingNote = () => nodes(quotaTree).find(n => n.type === 'p' && String(n.props.className).startsWith('radar-loading'));
      radarImageLoaded('a');  // the shown image
      runUntil(11900);
      assert.deepEqual(preloads, [], 'The next image waits 12 s after the shown one was fetched');
      runUntil(12100);
      assert.deepEqual(preloads, ['b'], 'The next image is fetched ahead while playback is paused');
      const quotaSpeed = () => nodes(quotaTree).find(n => n.type === 'button' && n.props['aria-label'] === 'เพิ่มความเร็ว');
      quotaSpeed().props.onClick(); quotaTree = quotaApp.render(quotaProps());
      quotaSpeed().props.onClick(); quotaTree = quotaApp.render(quotaProps());
      nodes(quotaTree).find(n => n.type === 'button').props.onClick(); quotaTree = quotaApp.render(quotaProps());
      advance(0);
      runUntil(17000);
      assert.equal(quotaSelection, 0, 'Requested is not loaded: playback holds even at 5x');
      assert(loadingNote()?.props.className.includes('is-waiting'), 'The bar says it is waiting for an image');
      assert.deepEqual(preloads, ['b'], 'Nothing else is fetched while an image is still loading');
      radarImageFailed('b');  // the map reported a tile error (Dashboard also clears the preload layer)
      runUntil(23900);
      assert.deepEqual(preloads, ['b'], 'A retry still waits its 12 s turn');
      runUntil(24100);
      assert.deepEqual(preloads, ['b', 'b'], 'A failed image is fetched again');
      assert.equal(quotaSelection, 0);
      radarImageLoaded('b');
      runUntil(24400);
      assert.equal(quotaSelection, 1, 'Playback continues once the map reports the image');
      runUntil(36100);
      assert.deepEqual(preloads, ['b', 'b', 'c']);
      runUntil(66000);
      assert.equal(quotaSelection, 1, 'An image that never arrives keeps playback waiting until it times out');
      runUntil(68100);
      assert.deepEqual(preloads.slice(3), [null, 'c'], 'A stuck fetch is cleared and tried again');
      radarImageLoaded('c');
      runUntil(68400);
      assert.equal(quotaSelection, 2);
      assert(!loadingNote(), 'The loading note goes once every image is loaded');
      const back = clock;
      runUntil(back - quotaStart + 2500);
      assert.equal(quotaSelection, 0, 'A loaded image comes back at full speed (2.4 s a frame at 5x)');
      nodes(quotaTree).find(n => n.type === 'button').props.onClick(); quotaApp.render(quotaProps());
      performance.now = savedPerformanceNow;

      const markerTree = i => instance(RadarPlayback).render({ ...currentProps(), index: i });
      const markerAt = i => nodes(markerTree(i)).find(n => n.props.className === 'radar-slider-time');
      assert(markerAt(0), 'A time label is shown above the slider thumb');
      for (const i of [0, 1, 2]) {
        const expected = new Intl.DateTimeFormat('th-TH', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Bangkok' }).format(currentFrames[i].time * 1000);
        assert.equal(markerAt(i).props.children, expected, 'The thumb label updates to the selected frame time');
        assert.equal(nodes(markerTree(i)).find(n => n.type === 'input').props['aria-valuetext'], expected);
      }
      assert.notEqual(markerAt(0).props.style.left, markerAt(2).props.style.left, 'The time label follows the selected thumb position');
      const singleFrame = instance(RadarPlayback).render({ ...currentProps(), frames: [currentFrames[1]], index: 0 });
      assert(!String(nodes(singleFrame).find(n => n.props.className === 'radar-slider-time').props.style.left).includes('NaN'), 'A single frame keeps a valid label position');




      nodes(enabledTree).find(n => n.type.name === 'RadarPlayback').props.onChange(0);
      radarFrames = [
        { tiles: 'https://tiles.test/older/{z}/{x}/{y}.png', time: 1799992800 },
        { ...radarFrames[0], tiles: 'https://tiles.test/refreshed/{z}/{x}/{y}.png' },
        { tiles: 'https://tiles.test/latest/{z}/{x}/{y}.png', time: 1800003600 },
      ];
      radarApp.render(props);
      const refreshRadar = radarEffects.filter(effect => /\.radarNowcast\(/.test(String(effect))).at(-1)();
      await tick();
      refreshRadar?.();
      let refreshedTree = radarApp.render(props);
      refreshedTree = radarApp.render(props);
      assert.equal(nodes(refreshedTree).find(n => n.type.name === 'RadarPlayback').props.index, 1, 'Radar refresh preserves the selected frame timestamp when it remains available');
      nodes(refreshedTree).find(n => n.type.name === 'RadarPlayback').props.onChange(0);
      radarFrames = [radarFrames[1], radarFrames[2]];
      radarApp.render(props);
      const removedRadarFrame = radarEffects.filter(effect => /\.radarNowcast\(/.test(String(effect))).at(-1)();
      await tick();
      removedRadarFrame?.();
      let missingFrameTree = radarApp.render(props);
      missingFrameTree = radarApp.render(props);
      assert.equal(nodes(missingFrameTree).find(n => n.type.name === 'RadarPlayback').props.index, 1, 'Radar refresh selects the latest frame if the selected timestamp expired');
      nodes(missingFrameTree).find(n => n.type.name === 'RadarPlayback').props.onChange(2);
      const pickedForecastTime = radarFrames.at(-1).time + 600;
      radarFrames = [radarFrames[1], { tiles: 'https://tiles.test/plus10/{z}/{x}/{y}.png', time: pickedForecastTime }, { tiles: 'https://tiles.test/plus20/{z}/{x}/{y}.png', time: pickedForecastTime + 600 }];
      radarApp.render(props);
      const realAtForecastTime = radarEffects.filter(effect => /\.radarNowcast\(/.test(String(effect))).at(-1)();
      await tick();
      realAtForecastTime?.();
      let realAtForecastTree = radarApp.render(props);
      realAtForecastTree = radarApp.render(props);
      assert.equal(nodes(realAtForecastTree).find(n => n.type.name === 'RadarPlayback').props.index, 2, 'A selected forecast does not turn into the real frame with the same time; the latest real frame is shown');

      lib.api.radarNowcast = async () => { throw new Error('offline'); };
      radarApp.render(props);
      const failRadar = radarEffects.filter(effect => /\.radarNowcast\(/.test(String(effect))).at(-1)();
      await tick();
      failRadar?.();
      const failedTree = radarApp.render(props);
      assert(nodes(failedTree).some(n => String(n.props.className || '').split(/\s+/).includes('radar-note') && content(n).includes('RainViewer')), 'Nowcast failure keeps the observed radar visible');
      lib.api.radarNowcast = async () => { radarRequests++; return { enabled: radarEnabled, status: 'unavailable', past_frames: [], forecast_frames: [], motion: null, rain_cells: [], explanation: '', explanation_source: 'template' }; };
    }
    cleanupRadar?.();
  }
  // A delayed lightweight-endpoint failure must not discard successful nowcast history.
  const savedRadarApi = lib.api.radar, savedNowcastApi = lib.api.radarNowcast;
  for (const failFirst of [false, true]) {
    let rejectObserved;
    lib.api.radar = () => new Promise((_, reject) => { rejectObserved = reject; });
    let resolveNowcast;
    lib.api.radarNowcast = () => new Promise(resolve => { resolveNowcast = resolve; });
    const effects = [], app = instance(Dashboard, effects);
    const props = { activeView: 'hazard', activeHazard: 'overview' };
    app.render(props);
    const cleanup = effects.find(effect => /\.radarNowcast\(/.test(String(effect)))();
    if (failFirst) { rejectObserved(new Error('timeout')); await tick(); }
    resolveNowcast({ enabled: true, past_frames: [{ tile_url: 'https://tiles.test/fallback.png', time: 1800000000 }], forecast_frames: [], motion: null, rain_cells: [], explanation: '', explanation_source: 'template' });
    await tick();
    if (!failFirst) { rejectObserved(new Error('timeout')); await tick(); }
    let tree = app.render(props);
    nodes(tree).find(n => n.props['data-layer'] === 'radar').props.onClick();
    tree = app.render(props);
    assert(nodes(tree).some(n => n.type.name === 'RadarPlayback' && n.props.frames.length === 1), 'Nowcast history survives observed endpoint failure in either response order');
    cleanup?.();
  }
  // The two endpoints cache RainViewer separately: forecasts always continue from the last frame on the slider.
  {
    const T = 1800000000;
    const frame = (time, name) => ({ tiles: `https://tiles.test/${name}.png`, time, kind: 'observed' });
    const nowcastAt = (latest, extra = {}) => ({ enabled: true, status: 'ok',
      past_frames: [latest - 600, latest].map(time => ({ tile_url: `https://tiles.test/n${time}.png`, time })),
      forecast_frames: [{ time: latest + 600, horizon_min: 10, dx_pixels: 1, dy_pixels: 0, cells: [{ id: 1, lat: 13.5, lon: 100.6 }] }],
      motion: { speed_kmh: 1, direction_deg: 90, direction_th: 'เกือบอยู่กับที่', is_moving: false },
      rain_cells: [{ id: 1, centroid_lat: 13.5, centroid_lon: 100.5, peak_intensity_dbz: 30 }],
      explanation: 'ข้อความแม่แบบ', explanation_source: 'template', ...extra });
    const run = async (radarResult, nowcastResult) => {
      lib.api.radar = radarResult instanceof Error ? async () => { throw radarResult; } : async () => radarResult;
      lib.api.radarNowcast = async () => nowcastResult;
      const effects = [], app = instance(Dashboard, effects), props = { activeView: 'hazard', activeHazard: 'overview' };
      app.render(props);
      const cleanup = effects.find(effect => /\.radarNowcast\(/.test(String(effect)))();
      await tick();
      let tree = app.render(props);
      nodes(tree).find(n => n.props['data-layer'] === 'radar').props.onClick();
      tree = app.render(props);
      return { tree, app, props, cleanup, playback: nodes(tree).find(n => n.type.name === 'RadarPlayback') };
    };
    let r = await run({ enabled: true, frames: [frame(T - 1200, 'a'), frame(T - 600, 'b')] }, nowcastAt(T));
    assert.equal(r.playback.props.frames.filter(f => f.kind === 'observed').at(-1).time, T, 'A lagging /v1/radar gives way to the newer nowcast frames');
    assert.equal(r.playback.props.frames.find(f => f.kind === 'forecast').imageTime, T, 'The +10 frame shows the image of the last frame on the slider');
    r.cleanup?.();
    r = await run({ enabled: true, frames: [frame(T - 600, 'a'), frame(T, 'b'), frame(T + 600, 'c')] }, nowcastAt(T));
    assert.equal(r.playback.props.frames.at(-1).time, T + 600, 'A newer /v1/radar ends the slider at its latest frame');
    assert(!r.playback.props.frames.some(f => f.kind === 'forecast'), 'A forecast from an older frame is not appended after a newer real one');
    r.cleanup?.();
    r = await run(new Error('timeout'), nowcastAt(T));
    assert.equal(r.playback.props.frames.filter(f => f.kind === 'observed').at(-1).time, T, 'A failed /v1/radar uses the nowcast frames');
    r.playback.props.onChange(2);
    const stationary = nodes(r.app.render(r.props)).find(n => 'radarForecast' in n.props).props.radarForecast;
    assert.deepEqual(stationary.features.map(f => f.properties.kind), ['position'], 'Rain the backend calls stationary is drawn at its projected position without a track');
    r.cleanup?.();
    // Qwen still writing: one more request after the wait, and nothing when the answer is final.
    const savedSetTimeout = global.setTimeout, timers = [];
    global.setTimeout = (fn, ms) => { timers.push({ fn, ms }); return timers.length; };
    let nowcastCalls = 0;
    lib.api.radar = async () => ({ enabled: true, frames: [] });
    lib.api.radarNowcast = async () => (++nowcastCalls === 1 ? nowcastAt(T, { explanation_pending: true }) : nowcastAt(T, { explanation: 'ข้อความจาก Qwen', explanation_source: 'qwen' }));
    const effects = [], app = instance(Dashboard, effects), props = { activeView: 'hazard', activeHazard: 'overview' };
    app.render(props);
    const cleanup = effects.find(effect => /\.radarNowcast\(/.test(String(effect)))();
    await tick();
    assert.equal(timers.filter(t => t.ms === 15000).length, 1, 'A pending explanation schedules one more nowcast request');
    timers.find(t => t.ms === 15000).fn();
    await tick();
    assert.equal(nowcastCalls, 2, 'The retry asks the backend again');
    let explanationTree = app.render(props);
    nodes(explanationTree).find(n => n.props['data-layer'] === 'radar').props.onClick();
    explanationTree = app.render(props);
    assert.equal(nodes(explanationTree).find(n => n.type.name === 'RadarPlayback').props.explanationSource, 'qwen', 'The late Qwen text reaches the open page');
    assert.equal(timers.filter(t => t.ms === 15000).length, 1, 'A final answer schedules nothing more');
    cleanup?.();
    global.setTimeout = savedSetTimeout;
  }
  lib.api.radar = savedRadarApi; lib.api.radarNowcast = savedNowcastApi;
  radarEnabled = false;
  const disabledRadarEffects = [];
  const disabledRadarApp = instance(Dashboard, disabledRadarEffects);
  const disabledRadarProps = { activeView: 'hazard', activeHazard: 'overview' };
  disabledRadarApp.render(disabledRadarProps);
  const cleanupDisabledRadar = disabledRadarEffects.find(effect => /\.radarNowcast\(/.test(String(effect)))();
  await tick();
  assert(!nodes(disabledRadarApp.render(disabledRadarProps)).some(n => n.props['data-layer'] === 'radar'), 'Overview radar toggle stays hidden when backend disables it');
  assert(!nodes(disabledRadarApp.render(disabledRadarProps)).some(n => String(n.props.className || '').split(/\s+/).includes('radar-note')), 'Radar note stays hidden when backend disables radar');
  cleanupDisabledRadar?.();
  // Issue 288/2569: the overview lists the TMD bulletin by region, with the level its wording gives each one.
  lib.api.warnings = async () => ({ warnings: [{ title: 'อากาศแปรปรวนบริเวณประเทศไทยตอนบน ฉบับที่ 5', url: 'https://tmd.go.th/x.pdf', hazards: ['rain_storm', 'flood'], by_region: [
    { from: '2026-11-05', to: '2026-11-07', level: 'WATCH', hazards: ['rain_storm'], areas: ['ประเทศไทยตอนบน'] },
    { from: '2026-11-07', to: '2026-11-09', level: 'WATCH', hazards: ['rain_storm', 'flood'], areas: ['ภาคใต้'] },
  ], rain_days: [
    { day: '2026-11-05', very_heavy: false, provinces: Array.from({ length: 45 }, (_, i) => 'จังหวัด' + i) },
    { day: '2026-11-08', very_heavy: true, provinces: ['นครศรีธรรมราช', 'สงขลา'] },
  ] }] });
  const bulletinEffects = [];
  const bulletinApp = instance(Dashboard, bulletinEffects);
  const bulletinProps = { activeView: 'hazard', activeHazard: 'overview' };
  bulletinApp.render(bulletinProps);
  const cleanupBulletin = bulletinEffects.find(effect => /\.warnings\(/.test(String(effect)))();
  await tick();
  const regionLines = nodes(bulletinApp.render(bulletinProps)).filter(n => n.type === 'li' && /ประเทศไทยตอนบน|ภาคใต้/.test(content(n)));
  assert.deepEqual(regionLines.map(n => [content(n).includes('ภาคใต้'), nodes(n).find(c => String(c.props.className || '').includes('risk-badge')).props.className]),
    [[false, 'risk-badge level-WATCH'], [true, 'risk-badge level-WATCH']], 'Overview lists the TMD bulletin by region with its level');
  // Daily tables: long lists as a count, short ones by name; very heavy rain is a Warning.
  const dayLines = nodes(bulletinApp.render(bulletinProps)).filter(n => n.type === 'li' && /ฝนตกหนัก/.test(content(n)));
  assert.deepEqual(dayLines.map(n => [content(n).includes('45 จังหวัด'), content(n).includes('นครศรีธรรมราช, สงขลา'), nodes(n).find(c => String(c.props.className || '').includes('risk-badge')).props.className]),
    [[true, false, 'risk-badge level-WATCH'], [false, true, 'risk-badge level-WARNING']], 'Overview lists the daily heavy-rain tables');
  cleanupBulletin?.();
  let opened = 0;
  const app = instance(Dashboard);
  let props = { activeView: 'hazard', searchInHeader: true, searchTarget: {}, riverViewRequest: 0 };
  let tree = app.render(props);
  const starter = nodes(tree).find(n => n.type === 'button' && content(n).startsWith('ค้นหาพื้นที่'));
  assert(!starter, 'header search replaces the empty-area search button');
  assert.equal(nodes(tree).find(n => n.props.mode === 'hazard' && 'dark' in n.props).props.dark, true, 'new browsers default to the dark basemap');

  // User searches/selects a province, then opens the province station list.
  nodes(tree).find(n => n.type === 'input' && n.props.id === 'area-search').props.onChange({ target: { value: 'ปทุมธานี' } });
  tree = app.render(props);
  await nodes(tree).find(n => n.type === 'form' && n.props.role === 'search').props.onSubmit({ preventDefault() {} });
  tree = app.render(props);
  nodes(tree).find(n => n.type === 'button' && content(n).includes('ปทุมธานี')).props.onClick();
  await tick();
  tree = app.render(props);
  const callout = nodes(tree).find(n => n.type === 'button' && /river-callout/.test(n.props.className || ''));
  assert(callout, 'province river callout available');
  callout.props.onClick();
  props = { ...props, activeView: 'rivers' };
  tree = app.render(props);
  assert(nodes(tree).some(n => n.props.className === 'location-scope'), 'province shortcut keeps its province filter');
  props = { ...props, activeView: 'hazard' }; app.render(props);
  props = { ...props, activeView: 'rivers', riverViewRequest: 1 };
  tree = app.render(props);
  assert(nodes(tree).some(n => n.props.className === 'location-scope'), 'top river category preserves selected location');
  props = { ...props, activeView: 'hazard' }; tree = app.render(props);
  nodes(tree).find(n => n.type === 'button' && /river-callout/.test(n.props.className || '')).props.onClick();
  props = { ...props, activeView: 'rivers' }; tree = app.render(props);
  assert(nodes(tree).some(n => n.props.className === 'location-scope'), 'a new province shortcut still works after reset');
  props = { ...props, riverViewRequest: 2 }; tree = app.render(props);
  assert(nodes(tree).some(n => n.props.className === 'location-scope'), 'reselecting rivers preserves the province filter');

  const landing = instance(load('@/components/LandingExperience').default);
  tree = landing.render();
  const savedLocation = window.location;
  let homeDestination;
  window.location = { assign: url => { homeDestination = url; } };
  nodes(tree).find(n => n.props.className === 'journey-brand').props.onClick({ preventDefault() {} });
  assert.equal(homeDestination, '/', 'Clicking the brand reloads the homepage with all optional layers off');
  window.location = savedLocation;
  const headerSearch = nodes(tree).find(n => n.type === 'button' && n.props.className === 'journey-search-icon');
  assert(headerSearch, 'header search entry remains available');
  assert.equal(content(headerSearch), 'พื้นที่ที่คุณอยากรู้', 'visible search label is the accessible name');
  assert.equal(headerSearch.props['aria-label'], undefined, 'do not override the visible search label');
  const searchDialog = nodes(tree).find(n => n.type === 'dialog' && n.props.className === 'journey-search-dialog');
  assert(searchDialog, 'search dialog remains available');
  searchDialog.props.ref.current = { showModal() { opened++; } };
  headerSearch.props.onClick();
  assert.equal(opened, 1, 'header search button opens the search dialog');
  const groups = nodes(tree).filter(n => n.type === 'button' && /journey-category-button/.test(n.props.className || ''));
  assert.deepEqual(groups.map(n => n.props['data-category']), lib.GROUPS.map(g => g.id), 'top bar shows every map group');
  // Opening rivers from the dashboard's sub-tabs makes the water group return there, with the same selected location.
  nodes(tree).find(n => n.type === Dashboard).props.onViewChange('rivers'); tree = landing.render();
  nodes(tree).find(n => n.props['data-category'] === 'overview').props.onClick(); tree = landing.render();
  assert.equal(nodes(tree).find(n => n.type === Dashboard).props.activeView, 'hazard', 'overview group opens the overview map');
  nodes(tree).find(n => n.props['data-category'] === 'water').props.onClick(); tree = landing.render();
  const dashboard = nodes(tree).find(n => n.type === Dashboard).props;
  assert.equal(dashboard.activeView, 'rivers', 'water group returns to the last opened item');
  assert.equal(dashboard.riverViewRequest, undefined, 'category changes do not reset selected location');
  const source = fs.readFileSync(path.join(root, 'src/components/Dashboard.tsx'), 'utf8');
  for (const unused of ['initialQuery', 'initialHazard', 'initialView']) assert(!source.includes(unused), 'unused prop removed: ' + unused);
  assert(!content(tree).includes('ผลการค้นหาในแดชบอร์ด'), 'guide describes current dialog flow');
  const { OverlayControls, OverlayLegends, DEFAULT_OVERLAYS } = load('@/components/OverviewLayers');
  assert.deepEqual(DEFAULT_OVERLAYS, { flood: false, cyclones: false, waves: false });
  const overlay = (status, count = 1) => ({ type: 'FeatureCollection', features: Array.from({length: count}, () => ({type: 'Feature', geometry: null, properties: {}})), overlay: {status, source: 'Test provider', updated_at: '2026-10-22T12:00:00Z'}, degraded_services: [] });
  let toggled;
  const controls = instance(OverlayControls);
  const controlProps = { data: {flood: overlay('unavailable', 0), cyclones: overlay('ok', 0), waves: overlay('pending', 0)}, enabled: { ...DEFAULT_OVERLAYS, cyclones: true }, onToggle: id => toggled = id };
  tree = controls.render(controlProps);
  assert(content(tree).includes('พายุหมุน') && content(tree).includes('ไม่พบพายุ'), 'cyclone control stays available and explains an empty successful layer');
  assert(nodes(tree).find(n => n.type === 'button' && content(n).includes('น้ำท่วม')).props.disabled, 'unavailable source disables layer with explanation');
  const waveToggle = nodes(tree).find(n => n.type === 'button' && content(n).includes('คลื่น'));
  assert(content(waveToggle).includes('กำลังโหลด'), 'pending layer has loading feedback');
  waveToggle.props.onClick();
  assert.equal(toggled, 'waves', 'toggle changes only the requested layer');
  tree = OverlayLegends({data: {flood: overlay('ok'), cyclones: overlay('ok', 0), waves: overlay('ok')}, enabled: {flood: true, cyclones: true, waves: false}});
  assert(content(tree).includes('Test provider') && content(tree).includes('เวลาไทย'), 'enabled layer credits include timestamp and source');
  assert(!content(tree).includes('เส้นทึบ') && !content(tree).includes('ลูกศร'), 'disabled and empty overlays have no legend');
  tree = OverlayLegends({data: {waves: {...overlay('ok'), degraded_services: ['open_meteo_waves']}}, enabled: {waves: true}});
  assert(content(tree).includes('ข้อมูลเดิม'), 'stale data is explicitly identified');
  const { useOverviewLayers } = load('@/components/OverviewLayers');
  const effects = [];
  const timers = [];
  const originalTimeout = global.setTimeout, originalClear = global.clearTimeout;
  global.setTimeout = (fn, delay) => { const timer = {fn, delay}; timers.push(timer); return timer; };
  global.clearTimeout = timer => { timer.cleared = true; };
  const calls = [];
  lib.api.overlay = async id => { calls.push(id); return overlay(id === 'waves' && calls.filter(x => x === id).length === 1 ? 'pending' : 'ok'); };
  const state = instance(() => useOverviewLayers(true, 0), effects);
  state.render();
  const cleanup = effects[0]();
  await tick();
  assert.deepEqual(calls, ['flood', 'cyclones', 'waves'], 'each overlay loads independently');
  assert.equal(state.render().data.waves.overlay.status, 'pending');
  assert.equal(timers[0].delay, 60000, 'pending overlay polls after one minute');
  await timers[0].fn(); await tick();
  assert.equal(state.render().data.waves.overlay.status, 'ok', 'pending layer recovers to ready');
  cleanup();
  assert(timers.every(timer => timer.cleared), 'leaving overview cancels polling');
  global.setTimeout = originalTimeout; global.clearTimeout = originalClear;
  const RiskSnapshot = load('@/components/RiskSnapshot').default;
  tree = RiskSnapshot({ total: null, counts: [] });
  assert.equal(nodes(tree).filter(n => n.type === 'strong' && content(n) === '—').length, 4, 'unloaded summary does not invent zero risk');
  tree = RiskSnapshot({ total: 928, counts: [{id:'CRITICAL',n:2,color:'#d7263d',th:'วิกฤต'}, {id:'WARNING',n:13,color:'#f77f00',th:'เตือนภัย'}, {id:'WATCH',n:41,color:'#f6c90e',th:'เฝ้าระวัง'}] });
  assert.deepEqual(nodes(tree).filter(n => n.type === 'strong').map(content), ['928','2','13','41'], 'summary retains the exact API-derived counts');
  assert(content(tree).includes('อำเภอในแผนที่') && content(tree).includes('เฝ้าระวัง'), 'summary uses explicit Thai labels');
  const scenic = instance(load('@/components/LandingExperience').default);
  const { riskAreasAtLevel } = load('@/lib/risk-overview');
  const polygon = {type:'Polygon',coordinates:[[[100,14],[102,14],[102,16],[100,16],[100,14]]]};
  const risks = {type:'FeatureCollection',features:[
    {type:'Feature',geometry:polygon,properties:{code:'TH2',name_th:'สอง',province_th:'ขอนแก่น',level:'WARNING',worst_hazard:'flood'}},
    {type:'Feature',geometry:polygon,properties:{code:'TH1',name_th:'หนึ่ง',province_th:'กระบี่',level:'WARNING',worst_hazard:'heat'}},
    {type:'Feature',geometry:polygon,properties:{code:'TH3',name_th:'สาม',level:'CRITICAL',worst_hazard:'fire'}}]};
  const warningAreas = riskAreasAtLevel(risks, 'WARNING');
  assert.deepEqual(warningAreas.map(a=>a.code), ['TH1','TH2'], 'risk list filters levels and sorts provinces');
  assert.deepEqual(warningAreas[0].center, [101,15], 'map points use district bounding centres');
  assert.equal(warningAreas[1].hazard, 'flood', 'list identifies the highest hazard');
  assert.deepEqual(riskAreasAtLevel(null, 'WATCH'), [], 'unavailable layers have no invented alerts');
  tree = scenic.render();
  assert(!nodes(tree).some(n => n.props.className === 'scenic-map-toggle'), 'map is always raised without an extra expand step');
  assert(nodes(tree).some(n => n.props.className === 'header-snapshot'), 'summary has its own header slot');
  assert(!nodes(tree).some(n => n.props.className === 'journey-top-cta'), 'Explore button is removed');
  assert(nodes(tree).some(n => n.props.className === 'journey-search-icon'), 'Area search stays available');
  const WaterQualityPanel = load('@/components/WaterQualityPanel').default;
  const i18n = load('@/lib/i18n');
  const previousDocument = global.document;
  global.document = {documentElement: {lang: 'th'}};
  const qualityStation = {id:'quality-1',name_th:'สถานีทดสอบ',river_th:'แม่น้ำทดสอบ',amphoe_th:'อำเภอทดสอบ',province_th:'จังหวัดทดสอบ',agency_th:'หน่วยงานทดสอบ',time:'2026-11-03T08:00:00Z',salinity_gl:0.8,do_mgl:2,ph:null,turbidity_ntu:null,tds_mgl:null,conductivity_uscm:null,temp_c:null,lat:13,lon:100,exceeds:{salinity:{text:'> 0.5'},do:{text:'< 3'}}};
  const qualityProps = {stations:[qualityStation],selected:qualityStation.id,onSelect(){},onClose(){},status:'success',degraded:false,source:'แหล่งข้อมูลทดสอบ',onRetry(){}};
  try {
    i18n.setLanguage('en');
    tree = WaterQualityPanel(qualityProps);
    const qualityStatus = nodes(tree).find(n => n.props.className === 'quality-status');
    assert.equal(content(qualityStatus), 'Above threshold: Salinity, Dissolved oxygen', 'dynamic threshold summary translates the prefix and each parameter');
    assert(content(tree).includes('สถานีทดสอบ') && content(tree).includes('แหล่งข้อมูลทดสอบ'), 'provider station/source names stay intact');
    assert(content(tree).includes('Province จังหวัดทดสอบ'), 'translated prefixes are separated from original place names');
    assert(content(tree).includes('0.80') && content(tree).includes('—'), 'measurements keep their values and missing metrics are not zero');
    assert(content(tree).includes('> 0.5') && content(tree).includes('< 3'), 'API threshold text stays intact');
    i18n.setLanguage('th');
    assert.equal(content(nodes(WaterQualityPanel(qualityProps)).find(n => n.props.className === 'quality-status')), 'เกินเกณฑ์: ความเค็ม, ออกซิเจนละลายน้ำ');
  } finally {
    i18n.setLanguage('th');
    global.document = previousDocument;
  }
  console.log('PASS: landing/search, overlays, radar nowcast timeline, risk summaries and water-quality TH/EN checks.');
})().catch(error => { console.error(error); process.exit(1); });
