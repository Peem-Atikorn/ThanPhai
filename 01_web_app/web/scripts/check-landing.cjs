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
  useEffect(fn) { current.cursor++; current.effects?.push(fn); },
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
function instance(Component, effects) {
  const state = [];
  return { state, render(props = {}) { current = { state, cursor: 0, effects }; this.tree = Component(props); return this.tree; } };
}
function nodes(node, result = []) {
  if (Array.isArray(node)) { node.forEach(n => nodes(n, result)); return result; }
  if (!node || typeof node !== 'object') return result;
  result.push(node); nodes(node.props?.children, result); return result;
}
function content(node) { if (Array.isArray(node)) return node.map(content).join(''); if (typeof node === 'string' || typeof node === 'number') return String(node); return node && typeof node === 'object' ? content(node.props?.children) : ''; }
const tick = () => new Promise(resolve => setImmediate(resolve));
global.requestAnimationFrame = fn => fn();
global.window = { matchMedia: () => ({ matches: true }) };
global.document = { getElementById: () => ({ focus() {}, scrollIntoView() {} }) };

(async () => {
  const lib = load('@/lib/api');
  lib.api.search = async () => { observed.search++; return [{ code: '13', level: 'province', name_th: 'ปทุมธานี', parents: [], lat: 14, lon: 100 }]; };
  lib.api.area = async () => ({ area: { code: '13', level: 'province', name_th: 'ปทุมธานี', parents: [] }, assessments: [], forecast: [], rivers: [{ status: 'NORMAL' }], river_province: { code: '13', name_th: 'ปทุมธานี' }, official_warnings: [], degraded_services: [] });
  lib.api.boundary = async () => null;
  const Dashboard = load('@/components/Dashboard').default;
  let radarRequests = 0;
  lib.api.radar = async () => { radarRequests++; return { enabled: true, frame: null }; };
  for (const hazard of ['overview', 'flood', 'rain_storm', 'tropical_cyclone']) {
    const radarEffects = [];
    const radarApp = instance(Dashboard, radarEffects);
    const radarTree = radarApp.render({ activeView: 'hazard', activeHazard: hazard });
    const before = radarRequests;
    const cleanupRadar = radarEffects.find(effect => /\.radar\(/.test(String(effect)))();
    assert.equal(radarRequests - before, 1, `Radar availability is checked for ${hazard}`);
    if (hazard === 'overview') assert(nodes(radarTree).some(n => n.type === 'button' && content(n).includes('เรดาร์บนแผนที่')), 'Overview has a radar toggle');
    cleanupRadar?.();
  }
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
  assert.deepEqual(DEFAULT_OVERLAYS, { flood: false, cyclones: true, waves: false });
  const overlay = (status, count = 1) => ({ type: 'FeatureCollection', features: Array.from({length: count}, () => ({type: 'Feature', geometry: null, properties: {}})), overlay: {status, source: 'Test provider', updated_at: '2026-10-22T12:00:00Z'}, degraded_services: [] });
  let toggled;
  const controls = instance(OverlayControls);
  const controlProps = { data: {flood: overlay('unavailable', 0), cyclones: overlay('ok', 0), waves: overlay('pending', 0)}, enabled: DEFAULT_OVERLAYS, onToggle: id => toggled = id };
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
  console.log('PASS: landing/search, overlays and polling, risk summaries, water-quality TH/EN thresholds and source/value preservation.');
})().catch(error => { console.error(error); process.exit(1); });
